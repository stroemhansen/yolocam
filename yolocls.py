import _thread as thread
import os
import json
import struct
import time
from datetime import datetime
from time import sleep
from ctypes import *
from enum import Enum
from pykson import JsonObject, IntegerField, FloatField, StringField, BooleanField, ObjectField, ObjectListField, ListField


class Color:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class LogType(Enum):
    DEBUG = 1
    WARNING = 2
    ERROR = 3
    NETWORK = 4
    DECISION = 5


class ColorType(Enum):
    BLACK_WHITE = 0
    COLOR = 1


class BoundsType(Enum):
    OK = 0
    PLATE_SIZE_MAX = 1
    PLATE_SIZE_MIN = 2
    TEXT_SCORE_LOW = 3
    PLATE_SCORE_LOW = 4
    PLATE_MARGIN_LEFT = 5
    PLATE_MARGIN_TOP = 6
    PLATE_MARGIN_RIGHT = 7
    PLATE_MARGIN_BOTTOM = 8


class DecisionModel(Enum):
    FREE_FLOW = 0
    ACCESS_CONTROL = 1


class SelectedDecision(Enum):
    FIRST = 0
    MIDDLE = 1
    LAST = 2


class InterfaceType(Enum):
    API = 0
    FILE = 1
    EXCEL = 2
    WEB_HOOK = 3
    FTP = 4
    SOCKET = 5


class AuxiliaryOutput(Enum):
    NONE = 0
    WHITELIST = 1
    BLACKLIST = 2
    RUNNING = 3
    NEW_PLATE = 4
    POSITION_ALARM = 5
    EXT_IR_LIGHT = 6


class AuxiliaryInput(Enum):
    NONE = 0
    LPR_DISABLED = 1


class DirectionType(Enum):
    front = 1
    rear = 2
    both = 3
    unknown = 4


class IrLightType(Enum):
    OFF = 0
    ON = 1
    AUTO = 2


class AuthenticationType(Enum):
    NONE = 0
    BASIC = 1
    DIGEST = 2
    PROXY = 3


class IoPin:
    def __init__(self, run=0, plate=2, warn=4, fan=6, ir=3, in1=1, out1=5, out2=7):
        self.RUN = run
        self.PLATE = plate
        self.WARN = warn
        self.FAN = fan
        self.IR = ir
        self.IN1 = in1
        self.OUT1 = out1
        self.OUT2 = out2


class Size(JsonObject):
    width = IntegerField(default_value=640)
    height = IntegerField(default_value=480)

    def __eq__(self, obj):
        try:
            return self.width == obj.width and self.height == obj.height
        except Exception:
            return False


class Margin(JsonObject):
    top = IntegerField(default_value=0)
    bottom = IntegerField(default_value=0)
    left = IntegerField(default_value=0)
    right = IntegerField(default_value=0)

    def __str__(self):
        return f'{self.top}; {self.bottom}; {self.left}; {self.right}'


class Position(JsonObject):
    x = IntegerField(default_value=0)
    y = IntegerField(default_value=0)
    z = IntegerField(default_value=0)

    def __init__(self, x=0, y=0, z=0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.x = x
        self.y = y
        self.z = z

    def __str__(self):
        return f'x={self.x}, y={self.y}, z={self.z}'


class Box(JsonObject):
    xMin = IntegerField(default_value=0, serialized_name="xmin")
    yMin = IntegerField(default_value=0, serialized_name="ymin")
    xMax = IntegerField(default_value=0, serialized_name="xmax")
    yMax = IntegerField(default_value=0, serialized_name="ymax")


class Rectangle:
    def __init__(self, box: any):
        try:
            if type(box).__name__ == 'Box':
                self.x: int = box.xMin
                self.y: int = box.yMin
                self.width: int = box.xMax - box.xMin
                self.height: int = box.yMax - box.yMin
            elif type(box).__name__ == 'dict':
                self.x: int = box['x']
                self.y: int = box['y']
                self.width: int = box['width']
                self.height: int = box['height']
            else:
                self.x: int = 0
                self.y: int = 0
                self.width: int = 0
                self.height: int = 0
        except (ValueError, Exception):
            self.x: int = 0
            self.y: int = 0
            self.width: int = 0
            self.height: int = 0

    def __str__(self):
        return f'[{self.x};{self.y};{self.width};{self.height}]'


class Region(JsonObject):
    score = FloatField(default_value=0.0)
    code = StringField(default_value='')


class IrLightControl(JsonObject):
    mode = IntegerField(default_value=0)
    brightnessThreshold = IntegerField(default_value=32)
    currentBrightness = IntegerField(default_value=0)


class Usage(JsonObject):
    calls = IntegerField(default_value=0)
    maxCalls = IntegerField(default_value=0, serialized_name="max_calls")


class Vehicle(JsonObject):
    score = FloatField(default_value=0.0)
    type = StringField(default_value='')
    box = Box()


class DeviceInterface(JsonObject):
    type = IntegerField(default_value=0)
    url = StringField(default_value='')
    authentication = IntegerField(default_value=0)
    username = StringField(default_value='')
    password = StringField(default_value='')
    options = StringField(default_value='')
    mailTo = StringField(default_value='')


class Email(JsonObject):
    host = StringField(default_value='www.smtp.dk')
    port = IntegerField(default_value=465)
    username = StringField(default_value='tm@stroemhansen.dk')
    password = StringField(default_value='tagrtqqors')
    subject = StringField(default_value='Yolocam license plate data')
    body = StringField(default_value='This email with attachment is sent from Yolocam')
    sender = StringField(default_value='YOLOCAM <noreply@smtp.dk>')
    recipients = ListField(str)
    attachment = StringField(default_value='')


class LprOptions(JsonObject):
    enabled = IntegerField(default_value=1)
    mmc = BooleanField(default_value=False)
    mode = StringField(default_value='')
    detection_rule = StringField(default_value='')
    detection_mode = StringField(default_value='')


class DecisionRecording(JsonObject):
    length = IntegerField(default_value=0)
    size = ObjectField(Size)
    infoText = BooleanField(default_value=True)
    outdated = IntegerField(default_value=7)


class SdkInformation(JsonObject):
    version = StringField(default_value='')
    licenseKey = StringField(serialized_name="license_key")


class Status(JsonObject):
    running = BooleanField(default_value=False)
    dockerRunning = BooleanField(default_value=False)
    cameraConnected = BooleanField(default_value=False)
    brightnessLevel = IntegerField(default_value=0)
    watchdog = 0


class Statistics(JsonObject):
    cameraFramesPerSecond = IntegerField(default_value=0)
    ocrFramesPerSecond = IntegerField(default_value=0)
    decisions = IntegerField(default_value=0)
    avgFrameSize = IntegerField(default_value=0)
    minFrameSize = IntegerField(default_value=0)
    maxFrameSize = IntegerField(default_value=0)
    avgLprTime = FloatField(default_value=0.0)
    minLprTime = FloatField(default_value=0.0)
    maxLprTime = FloatField(default_value=0.0)
    networkErrors = IntegerField(default_value=0)
    fatalErrors = IntegerField(default_value=0)
    reboots = IntegerField(default_value=0)
    unexpectedReboots = IntegerField(default_value=0)
    lastRebootTime = StringField(default_value='')


class Device(JsonObject):
    address = StringField(default_value='192.168.0.151')
    subnet = StringField(default_value='255.255.255.0')
    gateway = StringField(default_value='192.168.0.1')
    name = StringField(default_value='')
    model = StringField(default_value='')
    firmware = StringField(default_value='')
    dockerStatus = StringField(default_value='')
    sdkVersion = StringField(default_value='')
    sdkLicense = StringField(default_value='')
    sdkStatus = StringField(default_value='')
    sdkUsage = IntegerField(default_value=0)
    cpuName = StringField(default_value='')
    cpuFrequency = IntegerField(default_value=0)
    cpuTemperature = IntegerField(default_value=0)
    enclosureTemperature = IntegerField(default_value=0)
    fanTimeConsumption = IntegerField(default_value=0)
    usedMemory = StringField(default_value='')
    auxiliaryEnabled = BooleanField(default_value=False)
    gyroEnabled = BooleanField(default_value=False)
    enclosureTemperatureOption = IntegerField(default_value=0)  # 0=none, 1=gyro temperature sensor, 2=ext. temperature sensor
    utcTime = StringField(default_value='')


class Camera(JsonObject):
    changed = False
    id = ''
    address = StringField(default_value='')
    username = StringField(default_value='')
    password = StringField(default_value='')
    mountingAngle = IntegerField(default_value=0)
    resolution = ObjectField(Size)
    imageMask = StringField(default_value='')
    exposure = FloatField(default_value=0.0)
    brightness = FloatField(default_value=0.0)
    contrast = FloatField(default_value=0.0)
    hue = FloatField(default_value=0.0)
    saturation = FloatField(default_value=0.0)
    sharpness = FloatField(default_value=0.0)
    gamma = FloatField(default_value=0.0)
    gain = FloatField(default_value=0.0)
    irLightControl = ObjectField(IrLightControl)

    def __eq__(self, obj):
        try:
            return self.id == obj.id and \
                   self.address == obj.address and \
                   self.username == obj.username and \
                   self.password == obj.password and \
                   self.mountingAngle == obj.mountingAngle and \
                   self.resolution.width == obj.resolution.width and \
                   self.resolution.height == obj.resolution.height and \
                   self.imageMask == obj.imageMask and \
                   self.exposure == obj.exposure and \
                   self.brightness == obj.brightness and \
                   self.contrast == obj.contrast and \
                   self.hue == obj.hue and \
                   self.saturation == obj.saturation and \
                   self.sharpness == obj.sharpness and \
                   self.gamma == obj.gamma and \
                   self.gain == obj.gain
        except Exception:
            return False


class Lpr(JsonObject):
    region = StringField(default_value='')
    minRecognitions = IntegerField(default_value=0)
    frameRate = FloatField(default_value=0.0)
    frameHeight = IntegerField(default_value=0)
    selectedDecision = IntegerField(default_value=1)
    directionFilter = IntegerField(default_value=0)
    directionThreshold = IntegerField(default_value=0)
    decisionDelay = IntegerField(default_value=0)
    useCandidates = BooleanField(default_value=False)
    denyNumericDecision = BooleanField(default_value=True)
    minTextScore = FloatField(default_value=0.0)
    minPlateScore = FloatField(default_value=0.0)
    plateMargin = ObjectField(Margin)
    plateBlockingTime = IntegerField(default_value=0)
    resultExpireTime = IntegerField(default_value=0)
    maxPlateSize = ObjectField(Size)
    minPlateSize = ObjectField(Size)
    cropDecision = ObjectField(Size)
    includeFullImage = StringField(default_value='')
    decisionModel = IntegerField(default_value=0)
    deviceInterface = ObjectField(DeviceInterface)
    decisionRecording = DecisionRecording()
    options = ObjectField(LprOptions)
    currentPlate = ''


class VideoStream(JsonObject):
    enabled = BooleanField(default_value=False)
    color = IntegerField(default_value=0)
    compression = IntegerField(default_value=0)


class Auxiliary(JsonObject):
    input1 = IntegerField(default_value=0)
    output1 = IntegerField(default_value=0)
    output2 = IntegerField(default_value=0)
    pulseLength = FloatField(default_value=1.0)
    startFan = IntegerField(default_value=60)
    positionAlarm = IntegerField(default_value=0)


class Monitor(JsonObject):
    url = StringField(default_value='')
    username = StringField(default_value='')
    password = StringField(default_value='')


class AuxiliaryStatus(JsonObject):
    input1 = IntegerField(default_value=0)
    output1 = IntegerField(default_value=0)
    output2 = IntegerField(default_value=0)
    fan = IntegerField(default_value=60)
    irLight = IntegerField(default_value=0)
    position = ObjectField(Position)


class Firmware(JsonObject):
    autoUpdate = BooleanField(default_value=True)
    version = StringField(default_value='')
    latest = StringField(default_value='')


class SystemStatus(JsonObject):
    address = StringField(default_value='127.0.0.1')
    firmware = StringField(default_value='')
    decisions = IntegerField(default_value=0)
    sdkStatus = StringField(default_value='')
    cpuTemperature = IntegerField(default_value=0)
    enclosureTemperature = IntegerField(default_value=0)
    fanTime = StringField(default_value='0:00')
    networkErrors = IntegerField(default_value=0)
    fatalErrors = IntegerField(default_value=0)
    reboots = IntegerField(default_value=0)
    systemRunning = BooleanField(default_value=False)
    dockerRunning = BooleanField(default_value=False)
    cameraConnected = BooleanField(default_value=False)
    input1 = IntegerField(default_value=0)
    output1 = IntegerField(default_value=0)
    output2 = IntegerField(default_value=0)
    position = ObjectField(Position)


class CameraParameters(JsonObject):
    camera = ObjectField(Camera)
    lpr = ObjectField(Lpr)
    videoStream = ObjectField(VideoStream)
    auxiliary = ObjectField(Auxiliary)
    firmware = ObjectField(Firmware)
    monitor = ObjectField(Monitor)


class DeviceParameters(JsonObject):
    device = ObjectField(Device)
    status = ObjectField(Status)
    statistics = ObjectField(Statistics)
    auxiliary = ObjectField(AuxiliaryStatus)


class Candidate(JsonObject):
    score = FloatField(default_value=0.0)
    plate = StringField(default_value='')


class Result(JsonObject):
    timestamp = StringField(default_value='')
    plate = StringField(default_value='')
    box = Box()
    region = Region()
    vehicle = Vehicle()
    score = FloatField()
    dScore = FloatField(serialized_name="dscore")
    candidates = ObjectListField(Candidate)
    passed = False
    loops = 0
    expire: float = 0.0


class PlateReaderResult(JsonObject):
    filename = StringField(default_value='')
    timestamp = StringField(default_value='')
    cameraId = StringField(default_value='', serialized_name="camera_id")
    error = StringField()
    results = ObjectListField(Result)
    usage = Usage()
    processingTime = FloatField(default_value=0.0, serialized_name="processing_time")
    frame = None


class Decision:
    def __init__(self, address, guid, timestamp, plate, direction, score, dscore, rectangle, speed, region, vehicle, candidates, image, fullImage=None):
        self.address: str = address
        self.id: str = guid
        self.timestamp: str = timestamp
        self.plate: str = plate
        self.direction: str = direction
        self.score: float = score
        self.dscore: float = dscore
        self.x: int = rectangle.x
        self.y: int = rectangle.y
        self.width: int = rectangle.width
        self.height: int = rectangle.height
        self.speed: float = speed
        self.region: dict = self.__region_(region)
        self.vehicle: dict = self.__vehicle_(vehicle)
        self.candidates: list = self.__candidates_(candidates)
        self.image: str = image
        self.fullImage: str = fullImage

    @staticmethod
    def __region_(values):
        return dict(code=str(values.code).upper(), score=float(values.score))

    @staticmethod
    def __vehicle_(values):
        if values.box is None:
            return dict(type=str(values.type), score=float(values.score))
        else:
            return dict(type=str(values.type), score=float(values.score),
                        x=int(values.box.xMin), y=int(values.box.yMin), width=int(values.box.xMax) - int(values.box.xMin), height=int(values.box.yMax) - int(values.box.yMin))

    @staticmethod
    def __candidates_(values):
        buf = []
        for value in values:
            buf.append(dict(plate=str(value.plate).upper(), score=float(value.score)))
        return buf

    def to_json(self):
        return json.dumps(self.__dict__, separators=(',', ':'))

    def __str__(self):
        return f'[{self.plate,}], TIME={self.timestamp}, DIR={self.direction}, SCORE={self.score}, DSCORE={self.dscore}, SPEED={self.speed:.1f}, ' \
               f'RECT=[{self.x};{self.y};{self.width};{self.height}], ID={self.id}'


class GHF51:
    def __init__(self, direction=None, negate=0b00000000, path='/home/cam/libEAPI_Library.so'):
        # https://stackoverflow.com/questions/26363641/passing-a-pointer-value-to-a-c-function-from-python
        self._API = None
        self.init = False
        self.status = 0
        self.negate = negate

        if not os.path.isfile(path):
            self.status = 1  # Library file don't exists
        elif direction is None:
            self.status = 2  # I/O direction not defined
        else:
            try:
                self._API = CDLL(path)
                self.status = self._API.EApiLibInitialize()
                self.init = self.status == 0
                if self.init:
                    self.setDirection(direction)
                    thread.start_new_thread(self.__monitor, ())
            except (IndexError, ValueError, Exception) as e:
                self.status = 3  # Could not initialize API

    def setDirection(self, direction: int) -> bool:
        if self.init:
            f = self._API.EApiGPIOSetDirection
            f.argtypes = [c_uint, c_uint, c_uint]
            f.restype = c_int
            mask = 255
            return f(0x10000, mask, direction) == 0
        else:
            return False

    def setDigital(self, pin: int, value: int) -> bool:
        if self.init:
            f = self._API.EApiGPIOSetLevel
            f.argtypes = [c_uint, c_uint, c_uint]
            f.restype = c_int
            mask = 1 << pin
            if value == 0:
                v = 0 if not self.__negate(pin) else mask
            else:
                v = mask if not self.__negate(pin) else 0
            return f(0x10000, mask, v) == 0
        else:
            return False

    def getDigital(self, pin: int) -> int:
        if self.init:
            return self.__getLevel(pin)
        else:
            return -1

    def toggleDigital(self, pin: int) -> bool:
        if self.init:
            level = self.__getLevel(pin)
            if level == 0:
                return self.setDigital(pin, 1)
            else:
                return self.setDigital(pin, 0)
        else:
            return False

    def pulseDigital(self, pin: int, length: float) -> None:
        if self.init:
            thread.start_new_thread(self.__pulse, (pin, length))

    def getSystemTemperature(self, decimals=0) -> float:
        if self.init:
            f = self._API.EApiBoardGetValue
            f.argtypes = [c_uint, POINTER(c_uint)]
            f.restype = c_int
            v = c_uint()
            f(0x20002, byref(v))
            c = float((v.value / 10) - 273.15)
            if decimals == 0:
                return round(c)
            else:
                return round(c, decimals)
        else:
            return round(-1.0)

    def i2cProbeDevice(self, id: int) -> bool:
        try:
            f = self._API.EApiI2CProbeDevice
            f.argtypes = [c_uint, c_uint]
            f.restype = c_uint
            return f(0, id) == 0
        except Exception:
            return False

    def i2cReadBytes(self, id, register, length) -> (bool, list):
        buf = []
        try:
            f = self._API.EApiI2CReadTransfer
            f.argtypes = [c_uint, c_uint, c_uint, POINTER(c_char * 1), c_uint, c_uint]
            f.restype = c_uint
            c_arr = (c_char * 1)()
            rtn = True
            for i in range(length):
                if f(0, id, register + i, byref(c_arr), 1, 1) == 0:
                    buf.append(int.from_bytes(c_arr, "little"))
                else:
                    buf = [0 for i in range(length)]
                    rtn = False
                    break
            return rtn, buf[0] if length == 1 else buf
        except Exception:
            return False, buf[0] if length == 1 else buf

    def i2cWriteBytes(self, id: int, register: int, values) -> bool:
        try:
            f = self._API.EApiI2CWriteTransfer
            f.argtypes = [c_uint, c_uint, c_uint, (c_char * 1), c_uint]
            f.restype = c_uint
            if isinstance(values, int):
                values = [values]
            for value in values:
                c_arr = (c_char * 1)(value)
                if f(0, id, register, c_arr, 1) != 0:
                    return False
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self.init:
            self.init = False
            self._API.EApiLibUnInitialize()

    def __negate(self, pin: int) -> bool:
        return (self.negate & (1 << pin)) > 0

    def __getDirection(self) -> int:
        if self.init:
            f = self._API.EApiGPIOGetDirection
            f.argtypes = [c_uint, c_uint, POINTER(c_uint)]
            f.restype = c_int
            v = c_uint()
            mask = 255
            f(0x10000, mask, byref(v))
            return v.value  # 255 = all bits are inputs
        else:
            return -1

    def __getLevel(self, pin: int) -> int:
        if self.init:
            f = self._API.EApiGPIOGetLevel
            f.argtypes = [c_uint, c_uint, POINTER(c_uint)]
            f.restype = c_int
            v = c_uint()
            mask = 1 << pin
            f(0x10000, mask, byref(v))
            if v.value == 0:
                return 0 if not self.__negate(pin) else 1
            else:
                return 1 if not self.__negate(pin) else 0
        else:
            return 0

    def __pulse(self, pin: int, length: float) -> None:
        self.setDigital(pin, 1)
        sleep(length)
        self.setDigital(pin, 0)

    def __monitor(self) -> None:
        while self.init:
            sleep(30.0)
            self.status = self._API.EApiLibInitialize()
            if self.status != -1:
                self._API.EApiLibUnInitialize()
                self.status = self._API.EApiLibInitialize()


class MCP23008:
    class REG:
        ADDRESS = 0x20

        IODIR = 0x00
        IPOL = 0x01
        GPINTEN = 0x02
        DEFVAL = 0x03
        INTCON = 0x04
        IOCON = 0x05
        GPPU = 0x06
        INTF = 0x07
        INTCAP = 0x08
        GPIO = 0x09
        OLAT = 0x0A

    def __init__(self, board: GHF51 = None, id=None, direction=None, negate=0b00000000):
        self._board = board
        self._id = id
        self.negate = negate
        self.init = self.__probeDevice()
        self.setDirection(direction)

    def setDirection(self, direction) -> bool:
        if self.init:
            if direction is None:
                direction = 0b11111111
            self.__writeBytes(MCP23008.REG.IODIR, direction)  # Set direction
            self.__writeBytes(MCP23008.REG.GPPU, (direction ^ 0xFF))  # Set week pull-up resistors
            return True
        else:
            return False

    def setDigital(self, pin: int, value: int) -> bool:
        if self.init:
            data = self.__readBytes(MCP23008.REG.GPIO)
            if value if not self.__negate(pin) else value ^ 1 == 1:
                data = data | (1 << pin)  # Set bit
            else:
                data = data & ~(1 << pin)  # Reset bit
            return self.__writeBytes(MCP23008.REG.GPIO, [data])
        return False

    def getDigital(self, pin: int) -> int:
        if self.init:
            return self.__getLevel(pin)
        else:
            return -1

    def toggleDigital(self, pin: int) -> bool:
        if self.init:
            if self.__getLevel(pin) == 0:
                return self.setDigital(pin, 1)
            else:
                return self.setDigital(pin, 0)
        else:
            return False

    def pulseDigital(self, pin: int, length: float) -> None:
        if self.init:
            thread.start_new_thread(self.__pulse, (pin, length))

    def __probeDevice(self):
        rtn = self._board.i2cProbeDevice(self._id)
        if not rtn:
            self._rwFault = True
        return rtn

    def __negate(self, pin: int) -> bool:
        return (self.negate & (1 << pin)) > 0

    def __getLevel(self, pin: int) -> int:
        if self.init:
            data = self.__readBytes(MCP23008.REG.GPIO)
            value = data & (1 << pin)
            if value == 0:
                return 0 if not self.__negate(pin) else 1
            else:
                return 1 if not self.__negate(pin) else 0
        return 0

    def __pulse(self, pin: int, length: float) -> None:
        self.setDigital(pin, 1)
        sleep(length)
        self.setDigital(pin, 0)

    def __readBytes(self, register: int, length=1) -> any:
        rtn, data = self._board.i2cReadBytes(self._id, register, length)
        if not rtn:
            self._rwFault = True
        return data

    def __writeBytes(self, register: int, values) -> bool:
        rtn = self._board.i2cWriteBytes(self._id + 1, register, values)
        if not rtn:
            self._rwFault = True
        return rtn


class BNO055:
    class REGISTER:
        ADDRESS_A = 0x28  # 0x50
        ADDRESS_B = 0x29  # 0x52
        ID = 0xA0

        # Power mode settings
        POWER_MODE_NORMAL = 0X00
        POWER_MODE_LOWPOWER = 0X01
        POWER_MODE_SUSPEND = 0X02

        # Operation mode settings
        OPERATION_MODE_CONFIG = 0X00
        OPERATION_MODE_ACCONLY = 0X01
        OPERATION_MODE_MAGONLY = 0X02
        OPERATION_MODE_GYRONLY = 0X03
        OPERATION_MODE_ACCMAG = 0X04
        OPERATION_MODE_ACCGYRO = 0X05
        OPERATION_MODE_MAGGYRO = 0X06
        OPERATION_MODE_AMG = 0X07
        OPERATION_MODE_IMUPLUS = 0X08
        OPERATION_MODE_COMPASS = 0X09
        OPERATION_MODE_M4G = 0X0A
        OPERATION_MODE_NDOF_FMC_OFF = 0X0B
        OPERATION_MODE_NDOF = 0X0C

        # Output vector type
        VECTOR_ACCELEROMETER = 0x08
        VECTOR_MAGNETOMETER = 0x0E
        VECTOR_GYROSCOPE = 0x14
        VECTOR_EULER = 0x1A
        VECTOR_LINEARACCEL = 0x28
        VECTOR_GRAVITY = 0x2E

        # REGISTER DEFINITION START
        PAGE_ID = 0X07

        CHIP_ID = 0x00
        ACCEL_REV_ID = 0x01
        MAG_REV_ID = 0x02
        GYRO_REV_ID = 0x03
        SW_REV_ID_LSB = 0x04
        SW_REV_ID_MSB = 0x05
        BL_REV_ID = 0X06

        # Accel data register
        ACCEL_DATA_X_LSB = 0X08
        ACCEL_DATA_X_MSB = 0X09
        ACCEL_DATA_Y_LSB = 0X0A
        ACCEL_DATA_Y_MSB = 0X0B
        ACCEL_DATA_Z_LSB = 0X0C
        ACCEL_DATA_Z_MSB = 0X0D

        # Mag data register
        MAG_DATA_X_LSB = 0X0E
        MAG_DATA_X_MSB = 0X0F
        MAG_DATA_Y_LSB = 0X10
        MAG_DATA_Y_MSB = 0X11
        MAG_DATA_Z_LSB = 0X12
        MAG_DATA_Z_MSB = 0X13

        # Gyro data registers
        GYRO_DATA_X_LSB = 0X14
        GYRO_DATA_X_MSB = 0X15
        GYRO_DATA_Y_LSB = 0X16
        GYRO_DATA_Y_MSB = 0X17
        GYRO_DATA_Z_LSB = 0X18
        GYRO_DATA_Z_MSB = 0X19

        # Euler data registers
        EULER_H_LSB = 0X1A
        EULER_H_MSB = 0X1B
        EULER_R_LSB = 0X1C
        EULER_R_MSB = 0X1D
        EULER_P_LSB = 0X1E
        EULER_P_MSB = 0X1F

        # Quaternion data registers
        QUATERNION_DATA_W_LSB = 0X20
        QUATERNION_DATA_W_MSB = 0X21
        QUATERNION_DATA_X_LSB = 0X22
        QUATERNION_DATA_X_MSB = 0X23
        QUATERNION_DATA_Y_LSB = 0X24
        QUATERNION_DATA_Y_MSB = 0X25
        QUATERNION_DATA_Z_LSB = 0X26
        QUATERNION_DATA_Z_MSB = 0X27

        # Linear acceleration data registers
        LINEAR_ACCEL_DATA_X_LSB = 0X28
        LINEAR_ACCEL_DATA_X_MSB = 0X29
        LINEAR_ACCEL_DATA_Y_LSB = 0X2A
        LINEAR_ACCEL_DATA_Y_MSB = 0X2B
        LINEAR_ACCEL_DATA_Z_LSB = 0X2C
        LINEAR_ACCEL_DATA_Z_MSB = 0X2D

        # Gravity data registers
        GRAVITY_DATA_X_LSB = 0X2E
        GRAVITY_DATA_X_MSB = 0X2F
        GRAVITY_DATA_Y_LSB = 0X30
        GRAVITY_DATA_Y_MSB = 0X31
        GRAVITY_DATA_Z_LSB = 0X32
        GRAVITY_DATA_Z_MSB = 0X33

        # Temperature data register
        TEMPERATURE = 0X34

        # Status registers
        CALIB_STAT = 0X35
        SELFTEST_RESULT = 0X36
        INTR_STAT = 0X37

        SYS_CLK_STAT = 0X38
        SYS_STAT = 0X39
        SYS_ERR = 0X3A

        # Unit selection register
        UNIT_SEL = 0X3B
        DATA_SELECT = 0X3C

        # Mode registers
        OPR_MODE = 0X3D
        PWR_MODE = 0X3E

        SYS_TRIGGER = 0X3F
        TEMP_SOURCE = 0X40

        # Axis remap registers
        AXIS_MAP_CONFIG = 0X41
        AXIS_MAP_SIGN = 0X42

        # SIC registers
        SIC_MATRIX_0_LSB = 0X43
        SIC_MATRIX_0_MSB = 0X44
        SIC_MATRIX_1_LSB = 0X45
        SIC_MATRIX_1_MSB = 0X46
        SIC_MATRIX_2_LSB = 0X47
        SIC_MATRIX_2_MSB = 0X48
        SIC_MATRIX_3_LSB = 0X49
        SIC_MATRIX_3_MSB = 0X4A
        SIC_MATRIX_4_LSB = 0X4B
        SIC_MATRIX_4_MSB = 0X4C
        SIC_MATRIX_5_LSB = 0X4D
        SIC_MATRIX_5_MSB = 0X4E
        SIC_MATRIX_6_LSB = 0X4F
        SIC_MATRIX_6_MSB = 0X50
        SIC_MATRIX_7_LSB = 0X51
        SIC_MATRIX_7_MSB = 0X52
        SIC_MATRIX_8_LSB = 0X53
        SIC_MATRIX_8_MSB = 0X54

        # Accelerometer Offset registers
        ACCEL_OFFSET_X_LSB = 0X55
        ACCEL_OFFSET_X_MSB = 0X56
        ACCEL_OFFSET_Y_LSB = 0X57
        ACCEL_OFFSET_Y_MSB = 0X58
        ACCEL_OFFSET_Z_LSB = 0X59
        ACCEL_OFFSET_Z_MSB = 0X5A

        # Magnetometer Offset registers
        MAG_OFFSET_X_LSB = 0X5B
        MAG_OFFSET_X_MSB = 0X5C
        MAG_OFFSET_Y_LSB = 0X5D
        MAG_OFFSET_Y_MSB = 0X5E
        MAG_OFFSET_Z_LSB = 0X5F
        MAG_OFFSET_Z_MSB = 0X60

        # Gyroscope Offset registers
        GYRO_OFFSET_X_LSB = 0X61
        GYRO_OFFSET_X_MSB = 0X62
        GYRO_OFFSET_Y_LSB = 0X63
        GYRO_OFFSET_Y_MSB = 0X64
        GYRO_OFFSET_Z_LSB = 0X65
        GYRO_OFFSET_Z_MSB = 0X66

        # Radius registers
        ACCEL_RADIUS_LSB = 0X67
        ACCEL_RADIUS_MSB = 0X68
        MAG_RADIUS_LSB = 0X69
        MAG_RADIUS_MSB = 0X6A

    def __init__(self, board: GHF51 = None, id=None, extCrystal=True):
        self._board = board
        self._id = id
        self._extCrystal = extCrystal
        self._mode = BNO055.REGISTER.OPERATION_MODE_NDOF
        self._position = Position()
        self._rwFault = False
        self._monitoring = False
        self.isCalibrated = False
        self.init = False
        self.status = ''
        self.restart()

    def restart(self):
        self._rwFault = False
        self.isCalibrated = False
        self.init = False
        self.status = ''

        if self._board is None:
            self.status = 'No board assigned'
        elif self._id is None:
            self.status = 'I2C device id not assigned'
        else:
            try:
                # Check device is connected to the I2C bus
                if not self.__probeDevice():
                    self.status = 'BNO055 device not detected'
                else:
                    sleep(1.0)
                    # Make sure we have the right device
                    if self.__readBytes(BNO055.REGISTER.CHIP_ID) != BNO055.REGISTER.ID:
                        self.status = 'BNO055 device not found'
                        sleep(1.0)  # Wait for the device to boot up
                        if self.__readBytes(BNO055.REGISTER.CHIP_ID) != BNO055.REGISTER.ID:
                            self.status = 'BNO055 device not found'

                if self.status == '':
                    # Switch to config mode
                    self.setMode(BNO055.REGISTER.OPERATION_MODE_CONFIG)

                    # Trigger a reset and wait for the device to boot up again
                    self.__writeBytes(BNO055.REGISTER.SYS_TRIGGER, [0x20])
                    sleep(1.0)
                    while self.__readBytes(BNO055.REGISTER.CHIP_ID) != BNO055.REGISTER.ID:
                        sleep(0.01)
                    sleep(0.05)

                    # Set to normal power mode
                    self.__writeBytes(BNO055.REGISTER.PWR_MODE, [BNO055.REGISTER.POWER_MODE_NORMAL])
                    sleep(0.01)

                    self.__writeBytes(BNO055.REGISTER.PAGE_ID, [0])
                    self.__writeBytes(BNO055.REGISTER.SYS_TRIGGER, [0])
                    sleep(0.01)

                    # Set the requested mode
                    self.setMode(BNO055.REGISTER.OPERATION_MODE_NDOF)
                    sleep(0.02)

                    sleep(1.0)
                    self.setExternalCrystalUse(self._extCrystal)
                    thread.start_new_thread(self.__calibrate, ())
                    if not self._monitoring:
                        thread.start_new_thread(self.__monitor, ())
                    self.status = 'Ok'
                    self.init = True
                else:
                    self.init = False
            except Exception as e:
                self.status = str(e)
                self.init = False

    def setMode(self, mode):
        self._mode = mode
        self.__writeBytes(BNO055.REGISTER.OPR_MODE, [self._mode])
        sleep(0.03)

    def setExternalCrystalUse(self, value=True):
        prevMode = self._mode
        self.setMode(BNO055.REGISTER.OPERATION_MODE_CONFIG)
        sleep(0.025)
        self.__writeBytes(BNO055.REGISTER.PAGE_ID, [0x00])
        self.__writeBytes(BNO055.REGISTER.SYS_TRIGGER, [0x80] if value else [0x00])
        sleep(0.01)
        self.setMode(prevMode)
        sleep(0.02)

    def getSystemStatus(self):
        if self.init:
            self.__writeBytes(BNO055.REGISTER.PAGE_ID, [0])
            (sys_stat, sys_err) = self.__readBytes(BNO055.REGISTER.SYS_STAT, 2)
            self_test = self.__readBytes(BNO055.REGISTER.SELFTEST_RESULT)[0]
            return sys_stat, self_test, sys_err
        else:
            return 0, 0, 0

    def getRevInfo(self):
        if self.init:
            (accel_rev, mag_rev, gyro_rev) = self.__readBytes(BNO055.REGISTER.ACCEL_REV_ID, 3)
            sw_rev = self.__readBytes(BNO055.REGISTER.SW_REV_ID_LSB, 2)
            sw_rev = sw_rev[0] | sw_rev[1] << 8
            bl_rev = self.__readBytes(BNO055.REGISTER.BL_REV_ID)
            return accel_rev, mag_rev, gyro_rev, sw_rev, bl_rev
        else:
            return 0, 0, 0, 0, 0

    def getCalibration(self):
        if self.init:
            calData = self.__readBytes(BNO055.REGISTER.CALIB_STAT)
            return (calData >> 6) & 0x03, (calData >> 4) & 0x03, (calData >> 2) & 0x03, calData & 0x03
        else:
            return 0, 0, 0, 0

    def getVector(self, vectorType):
        if self.init:
            buf = self.__readBytes(vectorType, 6)
            xyz = struct.unpack('hhh', struct.pack('BBBBBB', buf[0], buf[1], buf[2], buf[3], buf[4], buf[5]))
            if vectorType == BNO055.REGISTER.VECTOR_MAGNETOMETER:
                scalingFactor = 16.0
            elif vectorType == BNO055.REGISTER.VECTOR_GYROSCOPE:
                scalingFactor = 900.0
            elif vectorType == BNO055.REGISTER.VECTOR_EULER:
                scalingFactor = 16.0
            elif vectorType == BNO055.REGISTER.VECTOR_GRAVITY:
                scalingFactor = 100.0
            else:
                scalingFactor = 1.0
            return True, list([i / scalingFactor for i in xyz])
        else:
            return False, None

    def getTemperature(self):
        if self.init:
            return self.__readBytes(BNO055.REGISTER.TEMPERATURE)
        else:
            return -1

    def getPosition(self):
        pos = Position()
        rtn, d = self.getVector(BNO055.REGISTER.VECTOR_EULER)
        if rtn:
            pos.x = abs(int(d[0]) - self._position.x)
            pos.y = abs(int(d[1]) - self._position.y)
            pos.z = abs(int(d[2]) - self._position.z)
            if pos.x >= 180:
                pos.x = 360 - pos.x
            if pos.y >= 180:
                pos.y = 360 - pos.y
            if pos.z >= 180:
                pos.z = 360 - pos.z
        return pos

    def calibrate(self):
        self.isCalibrated = False
        rtn, d = self.getVector(BNO055.REGISTER.VECTOR_EULER)
        if rtn:
            self._position.x = int(d[0])
            self._position.y = int(d[1])
            self._position.z = int(d[2])
            self.isCalibrated = True

    def getQuat(self):
        if self.init:
            buf = self.__readBytes(BNO055.REGISTER.QUATERNION_DATA_W_LSB, 8)
            wxyz = struct.unpack('hhhh', struct.pack('BBBBBBBB', buf[0], buf[1], buf[2], buf[3], buf[4], buf[5], buf[6], buf[7]))
            return True, tuple([i * (1.0 / (1 << 14)) for i in wxyz])
        else:
            return False

    def close(self):
        self.init = False
        self._monitoring = False

    def __probeDevice(self):
        rtn = self._board.i2cProbeDevice(self._id)
        if not rtn:
            self._rwFault = True
        return rtn

    def __readBytes(self, register: int, length=1):
        rtn, data = self._board.i2cReadBytes(self._id, register, length)
        if not rtn:
            self._rwFault = True
        return data

    def __writeBytes(self, register: int, values):
        rtn = self._board.i2cWriteBytes(self._id, register, values)
        if not rtn:
            self._rwFault = True
        return rtn

    def __calibrate(self):
        sleep(10.0)
        self.calibrate()

    def __monitor(self):
        self._monitoring = True
        while self._monitoring:
            sleep(2.0)
            if self.init:
                self.__probeDevice()
                if self._rwFault:
                    print('restart BNO055')
                    self.restart()
                    sleep(10.0)


class DockerContainer:
    def __init__(self, value: str):
        b = e = 0
        tmp = []
        while len(value) > 0 and e >= 0:
            e = value.find('  ', b)
            if e > b:
                tmp.append(value[b:e].strip())
            if e == -1 and b > 0:
                tmp.append(value[b:len(value)].strip())
            b = e + 1

        if len(tmp) >= 6:
            if len(tmp) >= 5:
                self.container_id = tmp[0]
                self.image = tmp[1]
                self.command = tmp[2]
                self.created = tmp[3]
                self.status = tmp[4]
            if len(tmp) == 6:
                self.ports = ''
                self.names = tmp[5]
            if len(tmp) == 7:
                self.ports = tmp[5]
                self.names = tmp[6]
        else:
            self.container_id = ''
            self.image = ''
            self.command = ''
            self.created = ''
            self.status = ''
            self.ports = ''
            self.names = ''

    def __str__(self):
        return f'{self.container_id}|{self.image}|{self.command}|{self.created}|{self.status}|{self.ports}|{self.names}'
