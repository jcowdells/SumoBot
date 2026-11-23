import socket  # Sends our commands over WiFi.
import struct  # Packs the numbers into the eight bytes our robot expects.
import time

import pygame  # Installed as pygame-ce, but still imported as pygame.

# This must match local_ip in the Arduino code. Set it once for your hotspot.
UDP_IP = "10.42.0.133"
UDP_PORT = 100  # Must match localPort in the Arduino code.


class DATA:
    left = 0
    right = 0
    ch1 = 90  # Servo angle. Start in the middle.
    ch2 = 0  # Channels 2 to 6 are unused by the default controls.
    ch3 = 0
    ch4 = 0
    ch5 = 0
    ch6 = 0


def constrain(value, minimum, maximum):
    # Keep a number inside the range we've given it.
    return max(minimum, min(maximum, value))


def print_packet():
    print(f"LEFT:  {DATA.left}\nRIGHT: {DATA.right}\nCH1:   {DATA.ch1}")


def make_packet():
    # b is a signed byte; B is unsigned. Two motor values, then six channels.
    return struct.pack(
        "!bb6B",
        constrain(DATA.left, -127, 127),
        constrain(DATA.right, -127, 127),
        constrain(DATA.ch1, 0, 180),
        constrain(DATA.ch2, 0, 255),
        constrain(DATA.ch3, 0, 255),
        constrain(DATA.ch4, 0, 255),
        constrain(DATA.ch5, 0, 255),
        constrain(DATA.ch6, 0, 255),
    )


def read_controls(keys):
    # Reset each time, so letting go of a key stops the motors.
    DATA.left = 0
    DATA.right = 0
    if keys[pygame.K_w]:
        DATA.left += 127
        DATA.right += 127
    if keys[pygame.K_s]:
        DATA.left -= 127
        DATA.right -= 127
    if keys[pygame.K_d]:
        DATA.left += 100
        DATA.right -= 100
    if keys[pygame.K_a]:
        DATA.left -= 100
        DATA.right += 100
    if keys[pygame.K_q]:
        DATA.ch1 = 70
    if keys[pygame.K_e]:
        DATA.ch1 = 110
    # The servo keeps its last angle when Q and E are released.


class AxisCorrection:
    NO_CORRECTION = lambda k: k
    MAX_CORRECTION = lambda m: lambda k: min(2.0 * ((k + 1.0) / (m + 1.0)) - 1.0, 1.0)
    DEADZONE = lambda d: lambda k: 0 if abs(k) < d else k


# mapping from -1.0 to 1.0 -> 0.0 to 1.0 using different methods.
class AxisMap:
    DEFAULT = lambda k: k
    LINEAR = lambda k: (k + 1.0) * 0.5
    REVERSE_LINEAR = lambda k: (1.0 - k) * 0.5
    QUADRATIC = lambda k: ((k * (k + 2.0)) + 1.0) * 0.25


class Axis:
    def __init__(self, axis_id, axis_map=AxisMap.DEFAULT, axis_correction=AxisCorrection.NO_CORRECTION):
        self.__axis_id = axis_id
        self.__axis_map = axis_map
        self.__axis_correction = axis_correction

    def get_axis_id(self):
        return self.__axis_id

    def get_range(self):
        return (self.__axis_map(-1.0), self.__axis_map(1.0))

    def read_controls(self, joystick):
        control = self.__axis_correction(joystick.get_axis(self.__axis_id))
        return self.__axis_map(control)


class Controller:
    EPSILON = 1e-4

    @staticmethod
    def __check_range(axis, a, b):
        axis_range = axis.get_range()
        if abs(a - axis_range[0]) > Controller.EPSILON: return False
        if abs(b - axis_range[1]) > Controller.EPSILON: return False
        return True

    def __init__(self, joystick, fwrd_axis: Axis, bkwd_axis: Axis, turn_axis: Axis, flip_button):
        # iterate over axes and ensure they are in range. (helpful for debugging i hope)
        for axis in (fwrd_axis, bkwd_axis, turn_axis):
            if axis.get_axis_id() >= joystick.get_numaxes():
                raise ValueError(f"Axis ID {axis.get_axis_id()} greater than number of axes on controller.")

        # check the ranges on the axes are correct
        if not Controller.__check_range(fwrd_axis, 0.0, 1.0):
            raise ValueError("Forward axis has incorrect output range.")

        if not Controller.__check_range(bkwd_axis, 0.0, 1.0):
            raise ValueError("Backward axis has incorrect output range.")

        if not Controller.__check_range(turn_axis, -1.0, 1.0):
            raise ValueError("Turn axis has incorrect output range.")

        self.__joystick = joystick
        self.__fwrd_axis = fwrd_axis
        self.__bkwd_axis = bkwd_axis
        self.__turn_axis = turn_axis

        self.__flip_button = flip_button

    def read_controls(self):
        # fwrd, bkwd: [0.0, 1.0]
        # turn: [-1.0, 1.0]
        fwrd = self.__fwrd_axis.read_controls(self.__joystick)
        bkwd = self.__bkwd_axis.read_controls(self.__joystick)
        turn = self.__turn_axis.read_controls(self.__joystick)

        print(f"{fwrd} {bkwd} {turn}")

        strength = fwrd - bkwd

        if strength != 0:
            # at 0 turn, left and right = 1.0
            # at -1.0 turn, left = -1.0, right = 1.0
            # at 1.0 turn, left = 1.0, right = -1.0
            left = strength * (1.0 + 2.0 * min(turn, 0.0))
            right = strength * (1.0 + 2.0 * min(-turn, 0.0))
        else:
            left = turn
            right = -turn

        DATA.left = int(left * 127.0)
        DATA.right = int(right * 127.0)

        print_packet()


def main():
    pygame.init()
    pygame.joystick.init()
    pygame.display.set_mode((300, 300))
    pygame.display.set_caption("VISIT www.breakfastbandits.net/sumobot !!!")
    clock = pygame.time.Clock()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # IPv4, using UDP.
    previous_packet = None
    last_sent = 0.0
    running = True

    joysticks = dict()

    # adjusting axes for
    fwrd_axis = Axis(5, AxisMap.LINEAR, AxisCorrection.MAX_CORRECTION(0.800))
    bkwd_axis = Axis(2, AxisMap.LINEAR, AxisCorrection.MAX_CORRECTION(0.420))
    turn_axis = Axis(0, AxisMap.DEFAULT, AxisCorrection.DEADZONE(0.100))

    controller_id = 0

    try:
        while running:
            # Process window events before checking which keys are held.
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                    joy
                # Handle hotplugging
                if event.type == pygame.JOYDEVICEADDED:
                    # This event will be generated when the program starts for every
                    # joystick, filling up the list without needing to create them manually.
                    joy = pygame.joystick.Joystick(event.device_index)
                    joysticks[joy.get_instance_id()] = Controller(joy, fwrd_axis, bkwd_axis, turn_axis, None)
                    print(f"Joystick {joy.get_instance_id()} connencted")

                if event.type == pygame.JOYDEVICEREMOVED:
                    del joysticks[event.instance_id]
                    print(f"Joystick {event.instance_id} disconnected")
            if not running:
                break

            if controller_id in joysticks.keys():
                controller = joysticks[controller_id]
                controller.read_controls()

            packet = make_packet()
            now = time.monotonic()  # Elapsed time, unaffected by clock changes.

            if packet != previous_packet or now - last_sent >= 0.1:
                sock.sendto(packet, (UDP_IP, UDP_PORT))
                previous_packet = packet
                last_sent = now
            clock.tick(60)  # Check keys often, without using an entire CPU core.
    finally:
        sock.close()
        pygame.quit()



## copy of original main
def main_original():
    pygame.init()
    pygame.display.set_mode((300, 300))
    pygame.display.set_caption("Sumobot - WASD: drive, Q/E: servo")
    clock = pygame.time.Clock()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # IPv4, using UDP.
    previous_packet = None
    last_sent = 0.0
    running = True

    try:
        while running:
            # Process window events before checking which keys are held.
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
            if not running:
                break

            read_controls(pygame.key.get_pressed())
            packet = make_packet()
            now = time.monotonic()  # Elapsed time, unaffected by clock changes.
            if packet != previous_packet or now - last_sent >= 0.1:
                print_packet()
                sock.sendto(packet, (UDP_IP, UDP_PORT))
                previous_packet = packet
                last_sent = now
            clock.tick(60)  # Check keys often, without using an entire CPU core.
    finally:
        sock.close()
        pygame.quit()



# This is a simple class that will help us print to the screen.
# It has nothing to do with the joysticks, just outputting the
# information.
class TextPrint:
    def __init__(self):
        self.reset()
        self.font = pygame.font.Font(None, 25)

    def tprint(self, screen, text):
        text_bitmap = self.font.render(text, True, (0, 0, 0))
        screen.blit(text_bitmap, (self.x, self.y))
        self.y += self.line_height

    def reset(self):
        self.x = 10
        self.y = 10
        self.line_height = 15

    def indent(self):
        self.x += 10

    def unindent(self):
        self.x -= 10


def test():
    # Set the width and height of the screen (width, height), and name the window.
    pygame.init()
    screen = pygame.display.set_mode((500, 700))
    pygame.display.set_caption("Joystick example")

    # Used to manage how fast the screen updates.
    clock = pygame.time.Clock()

    # Get ready to print.
    text_print = TextPrint()

    # This dict can be left as-is, since pygame will generate a
    # pygame.JOYDEVICEADDED event for every joystick connected
    # at the start of the program.
    joysticks = {}

    done = False
    while not done:
        # Event processing step.
        # Possible joystick events: JOYAXISMOTION, JOYBALLMOTION, JOYBUTTONDOWN,
        # JOYBUTTONUP, JOYHATMOTION, JOYDEVICEADDED, JOYDEVICEREMOVED
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                done = True  # Flag that we are done so we exit this loop.

            if event.type == pygame.JOYBUTTONDOWN:
                print("Joystick button pressed.")
                if event.button == 0:
                    joystick = joysticks[event.instance_id]
                    if joystick.rumble(0, 0.7, 500):
                        print(f"Rumble effect played on joystick {event.instance_id}")

            if event.type == pygame.JOYBUTTONUP:
                print("Joystick button released.")

            # Handle hotplugging
            if event.type == pygame.JOYDEVICEADDED:
                # This event will be generated when the program starts for every
                # joystick, filling up the list without needing to create them manually.
                joy = pygame.joystick.Joystick(event.device_index)
                joysticks[joy.get_instance_id()] = joy
                print(f"Joystick {joy.get_instance_id()} connencted")

            if event.type == pygame.JOYDEVICEREMOVED:
                del joysticks[event.instance_id]
                print(f"Joystick {event.instance_id} disconnected")

        # Drawing step
        # First, clear the screen to white. Don't put other drawing commands
        # above this, or they will be erased with this command.
        screen.fill((255, 255, 255))
        text_print.reset()

        # Get count of joysticks.
        joystick_count = pygame.joystick.get_count()

        text_print.tprint(screen, f"Number of joysticks: {joystick_count}")
        text_print.indent()

        # For each joystick:
        for joystick in joysticks.values():
            jid = joystick.get_instance_id()

            text_print.tprint(screen, f"Joystick {jid}")
            text_print.indent()

            # Get the name from the OS for the controller/joystick.
            name = joystick.get_name()
            text_print.tprint(screen, f"Joystick name: {name}")

            guid = joystick.get_guid()
            text_print.tprint(screen, f"GUID: {guid}")

            power_level = joystick.get_power_level()
            text_print.tprint(screen, f"Joystick's power level: {power_level}")

            # Usually axis run in pairs, up/down for one, and left/right for
            # the other. Triggers count as axes.
            axes = joystick.get_numaxes()
            text_print.tprint(screen, f"Number of axes: {axes}")
            text_print.indent()

            for i in range(axes):
                axis = joystick.get_axis(i)
                text_print.tprint(screen, f"Axis {i} value: {axis:>6.3f}")
            text_print.unindent()

            buttons = joystick.get_numbuttons()
            text_print.tprint(screen, f"Number of buttons: {buttons}")
            text_print.indent()

            for i in range(buttons):
                button = joystick.get_button(i)
                text_print.tprint(screen, f"Button {i:>2} value: {button}")
            text_print.unindent()

            hats = joystick.get_numhats()
            text_print.tprint(screen, f"Number of hats: {hats}")
            text_print.indent()

            # Hat position. All or nothing for direction, not a float like
            # get_axis(). Position is a tuple of int values (x, y).
            for i in range(hats):
                hat = joystick.get_hat(i)
                text_print.tprint(screen, f"Hat {i} value: {str(hat)}")
            text_print.unindent()

            text_print.unindent()

        # Go ahead and update the screen with what we've drawn.
        pygame.display.flip()

        # Limit to 30 frames per second.
        clock.tick(30)



if __name__ == "__main__":
    invalid = True
    while invalid:
        command = input("$ << ")
        args = command.split(" ")
        invalid = False
        if len(args) != 1:
            print("$ >> invalid, type help for list of commands")
            invalid = True
        elif args[0] == "help":
            print("$ >> look at the code for list of commands LOL!!")
        elif args[0] == "calibrate":
            pass
        elif args[0] == "test":
            test()
        elif args[0] == "main":
            main()
        elif args[0] == "original":
            main_orginal()
        else:
            print("$ >> invalid, type help for list of commands")
            invalid = True
