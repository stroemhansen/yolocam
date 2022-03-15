import argparse
import asyncio
import copy
import ftplib
import glob
import math
import pickle
import platform
import re as regx
import signal
import socket
import sys
import uuid
import hashlib
from base64 import b64encode, b64decode
from pathlib import Path
from openpyxl import Workbook

import cv2
import numpy as np
import requests
import requests.auth
import urllib3
import websockets
from PIL import Image
from netaddr import IPNetwork
from pykson import Pykson
import smtplib
import ssl

from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from yolocls import *

SDK_ADDRESS = '0.0.0.0:8100'  # SDK address
SDK_TOKEN = ''  # SDK token
SDK_LICENSE = ''  # SDK license key
DEV_PARAMS = DeviceParameters()  # Device object
CAM_PARAMS = CameraParameters()  # Camera object
BOARD = GHF51()  # GHF51 board I/O interface
GPIO = BOARD  # GPIO control
DIO = IoPin()  # GPIO pin configuration
GYRO = BNO055()  # Gyroscope
LOG_WRITES = []  # Log message file write buffer
LOG_MESSAGES = []  # Log message buffer
TRIGGERS = []  # Thread locks to trigger plate recognition
DECISIONS = []  # Decision buffer
READINGS = []  # Readings buffer
PLATES = {}  # Accepted plate buffer {plate, count}
IGNORED = {}  # Ignored plate buffer {plate, expired}
DIRECTIONS = {}  # Direction control {plate, {x, y, timestamp}
INFERENCE_BUFFER = []  # Statistics buffer for recognition time
FRAME_BUFFER = []  # Statistics buffer for frame size
POST_BUFFER = []  # Video buffer for frames after a decision is made
VIDEO_BUFFER = []  # Video buffer to record live decision
EXCEL_BUFFER = []  # Temporary buffer for decisions to be saved to Excel file
EXCEL_BUSY = False  # Writing to excel file is busy
BLACKLIST = []  # List of plates that are blacklisted
WHITELIST = []  # List of plates that are whitelisted
IGNORELIST = []  # List of plates that will be ignored
POST_DELAY = 1.0  # Delay when posting data to webhook, ftp or TCP
WATCHDOG = 0  # Watchdog for socket communication
NEW_PLATE = False  # Flag indicating a new plate recognition
INIT = False  # Device is initialized
STARTED = True  # Application running flag
ASCII = {'<NUL>': 0, '<SOH>': 1, '<STX>': 2, '<ETX>': 3, '<EOT>': 4, '<ENQ>': 5, '<ACK>': 6, '<BEL>': 7, '<BS>': 8, '<HT>': 9, '<LF>': 10, '<VT>': 11, '<FF>': 12, '<CR>': 13,
         '<SO>': 14, '<SI>': 15, '<DLE>': 16, '<DC1>': 17, '<DC2>': 18, '<DC3>': 19, '<DC4>': 20, '<NAK>': 21, '<SYN>': 22, '<ETB>': 23, '<CAN>': 24, '<EM>': 25, '<SUB>': 26,
         '<ESC>': 27, '<FS>': 28, '<GS>': 29, '<RS>': 30, '<US>': 31, '<DEL>': 127}


def parse_arguments() -> None:
    global SDK_TOKEN, SDK_LICENSE, SDK_ADDRESS

    ap = argparse.ArgumentParser(description='YOLOCAM License Plate Reader',
                                 epilog='Start application as: python yolocam.py --token f9a70edb707a6cd1f91175506de4b2abc3cb73e3 --license 6GFwyXUp9U --address 127.0.0.1:8100')
    ap.add_argument('-t', '--token', type=str, action='store', help='SDK token.', required=True)
    ap.add_argument('-l', '--license', type=str, action='store', help='SDK license key.', required=False)
    ap.add_argument('-a', '--address', type=str, action='store', help='SDK engine address and port number.', required=False)

    args = ap.parse_args()
    if args.license:
        SDK_LICENSE = args.license
    if args.token:
        SDK_TOKEN = args.token
    if args.address:
        SDK_ADDRESS = args.address


def signal_handling(signum, frame) -> None:
    global STARTED

    log(LogType.DEBUG, 'signal_handling', 'YOLOCAM STOPPING...')
    log(LogType.DEBUG, 'signal_handling', f'signum={signum}, frame={frame}')  # signal.SIGINT, signal.SIGTERM, signal.SIGTSTP
    STARTED = False


def clear_terminal() -> None:
    if platform.system() == 'Linux':
        os.system('clear')


def ping(address: str) -> bool:
    match = regx.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', address)
    if match is None:
        return True
    else:
        return os.system(f'ping -c 1 {str(match.group())}') == 0


def to_hex(value: int, length=8, prefix='0x') -> str:
    if length == 0:
        length = len(hex(value))
    s = '{:X}'.format(value & (2 ** 32 - 1)).zfill(length)[-length:]
    return prefix + s


def is_numeric(value: str) -> bool:
    if regx.match(r'[+-]?\d', value) is not None:
        return True
    else:
        return False


def is_url(url):
    x = regx.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', regx.IGNORECASE)
    return url is not None and bool(x.search(url))


def format_exception(value) -> str:
    if type(value).__name__ == 'str':
        return str(value)
    else:
        return f'<{value.__class__.__name__}>. {str(value)}'


def decisions_to_str() -> str:
    global DECISIONS

    buf = dict(pending=[], index=[], plate=[], id=[])
    for d in DECISIONS.copy():
        pending, index, data, id = [d['pending'], d['index'], d['data'], d['id']]
        buf['pending'].append(pending)
        buf['index'].append(index)
        buf['plate'].append(data.plate)
        if len(id) == 0:
            buf['id'].append('x')
        else:
            buf['id'].append(id[0])
    return str(buf)


def load_test_image() -> None:
    global FRAME_BUFFER

    print('load_test_image()')
    p = np.fromfile('CP29179-1.jpg', dtype=np.uint8)
    platerecognizer_recognize(p)
    FRAME_BUFFER[0] = p


def create_folders() -> None:
    try:
        if platform.system() == 'Linux':
            for folder in ['logs', 'decisions', 'excel', 'flushed', 'post', 'ftp', 'tcp', 'videos', 'lists', 'email']:
                if not os.path.exists(folder):
                    os.mkdir(folder)
                os.chmod(f'/home/cam/{folder}', 0o777)
    except Exception as e:
        log(LogType.ERROR, 'create_folders', e)


def remove_file(file: str) -> None:
    try:
        if os.path.isfile(file):
            os.remove(file)
            log(LogType.DEBUG, 'remove_file', file)
    except Exception as e:
        log(LogType.WARNING, 'remove_file', f'Error: {e}')


def get_work_dir(file: str) -> str:
    if platform.system() == 'Linux':
        return str(Path(__file__).resolve().parent) + '/' + file
    else:
        return str(Path().absolute()) + '\\' + file


def get_network_adapter() -> (bool, str, str, str):
    try:
        if platform.system() == 'Linux':
            a = os.popen('ip r | grep default | uniq').read().split()  # Get default adapter
            for b in os.popen('ip address show dev ' + a[4]).read().split('\n'):  # Get info for adapter
                if b.__contains__('inet') and b.__contains__('global'):
                    c = b.split(' ')
                    net = IPNetwork(c[5])
                    return True, str(net.ip), str(net.netmask), a[2]  # Address, netmask, gateway
            return False, '', '', ''
        else:
            return False, '', '', ''
    except Exception:
        return False, '', '', ''


def get_firmware_version() -> (bool, str):
    try:
        url = 'http://yolofirmware.shweb.dk/Api/GetVersion'
        headers = {"content-type": "application/json", "user-agent": "yolocam/1.1.0"}
        response = requests.post(url=url, headers=headers)
        if response.status_code == 200:
            js = json.loads(str(response.json()).replace("\'", "\"").replace("None", "null"))
            latest = js['version']
            if latest is None:
                return False, ''
            elif latest == '':
                return False, ''
            elif not len(latest.split('.')) == 3:
                pass
            return True, latest
        else:
            log(LogType.WARNING, 'get_firmware_version', f'API: {url}, response error. ({response.status_code} - {response.reason})')
            return False, ''
    except Exception as e:
        log(LogType.ERROR, 'get_firmware_version', e)
        return False, ''


def update_firmware() -> None:
    global STARTED

    try:
        url = 'http://yolofirmware.shweb.dk/Api/GetFirmware'
        usr = 'rW3o9MUUj3'
        pwd = 'ci1Xe5T4sz'
        headers = {"content-type": "application/json", "user-agent": "yolocam/1.1.0"}
        response = requests.post(url=url, headers=headers, auth=requests.auth.HTTPBasicAuth(usr, pwd))
        if response.status_code == 200:
            js = json.loads(str(response.json()).replace("\'", "\"").replace("None", "null"))
            latest = js['version']
            n = 0
            for file in js['files']:
                md5 = file['md5']
                name = get_work_dir(file['name'])
                data = b64decode(file['data'])
                if hashlib.md5(data).hexdigest() == md5:
                    try:
                        with open(name, 'w') as f:
                            f.write(data.decode('utf-8').replace("\r\n", "\n"))
                            n += 1
                    except Exception as e:
                        log(LogType.ERROR, 'update_firmware', e)
                else:
                    log(LogType.WARNING, 'update_firmware', f'File MD5 hash failed. [{hashlib.md5(data).hexdigest()}] <> [{md5}]')
            if n == 2:
                log(LogType.DEBUG, 'update_firmware', f'New firmware version {latest} is downloaded - Restarting camera...')
                sleep(1.0)
                STARTED = False
        else:
            log(LogType.WARNING, 'update_firmware', f'API: {url}, response error. ({response.status_code} - {response.reason})')
    except Exception as e:
        log(LogType.ERROR, 'update_firmware', e)


def load_dev_parameters() -> None:
    global DEV_PARAMS

    signal.signal(signal.SIGINT, signal_handling)  # Trigger on CTRL+C
    signal.signal(signal.SIGTERM, signal_handling)  # Trigger on REBOOT
    if platform.system() == 'Linux':
        signal.signal(signal.SIGTSTP, signal_handling)  # Trigger on CTRL+Z
        # signal.signal(signal.SIGKILL, signal_handling)
        # signal.signal(signal.SIGHUP, signal_handling)
        # signal.signal(signal.SIGCHLD, signal_handling)

    try:
        file = get_work_dir('yolodev.ini')
        if os.path.isfile(file):
            try:
                with open(file, 'r') as f:
                    DEV_PARAMS = Pykson().from_json(f.read(), DeviceParameters, accept_unknown=True)
            except Exception:
                DEV_PARAMS = DeviceParameters()

        if DEV_PARAMS.device is None:
            DEV_PARAMS.device = Device()
        if DEV_PARAMS.status is None:
            DEV_PARAMS.status = Status()
        if DEV_PARAMS.statistics is None:
            DEV_PARAMS.statistics = Statistics()
        if DEV_PARAMS.auxiliary is None:
            DEV_PARAMS.auxiliary = Auxiliary()

        rtn, adr, sub, gw = get_network_adapter()
        if rtn:
            DEV_PARAMS.device.address = adr
            DEV_PARAMS.device.subnet = sub
            DEV_PARAMS.device.gateway = gw
        DEV_PARAMS.device.dockerStatus = 'Waiting...'
        DEV_PARAMS.device.sdkVersion = ''
        DEV_PARAMS.device.sdkLicense = ''
        DEV_PARAMS.device.sdkStatus = ''
        DEV_PARAMS.statistics.minFrameSize = 0
        DEV_PARAMS.statistics.maxFrameSize = 0
        DEV_PARAMS.statistics.avgFrameSize = 0
        DEV_PARAMS.statistics.minLprTime = 0
        DEV_PARAMS.statistics.maxLprTime = 0
        DEV_PARAMS.statistics.avgLprTime = 0
        DEV_PARAMS.statistics.networkErrors = 0
        DEV_PARAMS.statistics.fatalErrors = 0
        DEV_PARAMS.status.running = False
        DEV_PARAMS.status.dockerRunning = False
        DEV_PARAMS.status.cameraConnected = False
        DEV_PARAMS.statistics.reboots += 1
        DEV_PARAMS.statistics.lastRebootTime = datetime.now().strftime('%d-%m-%Y %H:%M:%S')

        reset_fan_timer()
    except Exception as e:
        log(LogType.ERROR, 'load_dev_parameters', e)


def save_dev_parameters() -> None:
    global DEV_PARAMS

    try:
        data = Pykson().to_json(DEV_PARAMS)
        with open(get_work_dir('yolodev.ini'), 'w') as f:
            f.write(str(data))
            log(LogType.DEBUG, 'save_dev_parameters', data)
    except Exception as e:
        log(LogType.ERROR, 'save_dev_parameters', e)


def load_cam_parameters() -> str:
    global CAM_PARAMS

    try:
        file = get_work_dir('yolocam.ini')
        if os.path.isfile(file):
            with open(file, 'r') as f:
                cam = CAM_PARAMS.camera
                value = f.read()
                CAM_PARAMS = Pykson().from_json(value, CameraParameters, accept_unknown=True)
                if CAM_PARAMS.camera is None:
                    CAM_PARAMS.camera = Camera()
                if CAM_PARAMS.lpr is None:
                    CAM_PARAMS.lpr = Lpr()
                if CAM_PARAMS.videoStream is None:
                    CAM_PARAMS.videoStream = VideoStream()
                if CAM_PARAMS.auxiliary is None:
                    CAM_PARAMS.auxiliary = Auxiliary()
                if CAM_PARAMS.firmware is None:
                    CAM_PARAMS.firmware = Firmware()
                if CAM_PARAMS.monitor is None:
                    CAM_PARAMS.monitor = Monitor()

                if CAM_PARAMS.lpr.deviceInterface is None:
                    CAM_PARAMS.lpr.deviceInterface = DeviceInterface()
                if CAM_PARAMS.lpr.decisionRecording is None:
                    CAM_PARAMS.lpr.decisionRecording = DecisionRecording()
                    CAM_PARAMS.lpr.decisionRecording.size = Size()
                if CAM_PARAMS.lpr.options is None:
                    CAM_PARAMS.lpr.options = LprOptions()

                CAM_PARAMS.camera.changed = not CAM_PARAMS.camera.__eq__(cam)
                return value
        else:
            return ''
    except Exception as e:
        log(LogType.ERROR, 'load_cam_parameters', e)


def save_cam_parameters() -> None:
    global CAM_PARAMS

    try:
        data = Pykson().to_json(CAM_PARAMS)
        with open(get_work_dir('yolocam.ini'), 'w') as f:
            f.write(data)
            # CAM_PARAMS.camera.changed = True
            log(LogType.DEBUG, 'save_cam_parameters', data)
    except Exception as e:
        log(LogType.ERROR, 'save_cam_parameters', e)


def load_blacklist() -> int:
    global BLACKLIST

    try:
        BLACKLIST.clear()
        file = get_work_dir('lists/blacklist.txt')
        if os.path.isfile(file):
            with open(file, 'r') as f:
                for plate in f:
                    BLACKLIST.append(plate.strip())
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'load_blacklist', e)
        return -1  # Error


def save_blacklist(values: str) -> int:
    global BLACKLIST

    try:
        BLACKLIST = values.split('|')
        with open(get_work_dir('lists/blacklist.txt'), 'w') as f:
            for plate in BLACKLIST:
                f.write('%s\n' % plate)
        log(LogType.DEBUG, 'save_blacklist', f'Saving: [{BLACKLIST}]')
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'save_blacklist', e)
        return -1  # Error


def add_blacklist(value: str) -> int:
    global BLACKLIST

    try:
        if value not in BLACKLIST:
            BLACKLIST.append(value)
            with open(get_work_dir('lists/blacklist.txt'), 'w') as f:
                for plate in BLACKLIST:
                    f.write('%s\n' % plate)
            log(LogType.DEBUG, 'add_blacklist', f'Adding plate [{value}]')
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'add_blacklist', e)
        return -1  # Error


def load_whitelist() -> int:
    global WHITELIST

    try:
        WHITELIST.clear()
        file = get_work_dir('lists/whitelist.txt')
        if os.path.isfile(file):
            with open(file, 'r') as f:
                for plate in f:
                    WHITELIST.append(plate.strip())
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'load_whitelist', e)
        return -1  # Error


def save_whitelist(values: str) -> int:
    global WHITELIST

    try:
        WHITELIST = values.split('|')
        with open(get_work_dir('lists/whitelist.txt'), 'w') as f:
            for plate in WHITELIST:
                f.write('%s\n' % plate)
        log(LogType.DEBUG, 'save_whitelist', f'Saving: [{WHITELIST}]')
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'save_whitelist', e)
        return -1  # Error


def add_whitelist(value: str) -> int:
    global WHITELIST

    try:
        if value not in WHITELIST:
            WHITELIST.append(value)
            with open(get_work_dir('lists/whitelist.txt'), 'w') as f:
                for plate in WHITELIST:
                    f.write('%s\n' % plate)
            log(LogType.DEBUG, 'add_whitelist', f'Adding plate [{value}]')
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'add_whitelist', e)
        return -1  # Error


def load_ignorelist() -> int:
    global IGNORELIST

    try:
        IGNORELIST.clear()
        file = get_work_dir('lists/ignorelist.txt')
        if os.path.isfile(file):
            with open(file, 'r') as f:
                for plate in f:
                    IGNORELIST.append(plate.strip())
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'load_ignorelist', e)
        return -1  # Error


def save_ignorelist(values: str) -> int:
    global IGNORELIST

    try:
        IGNORELIST = values.split('|')
        with open(get_work_dir('lists/ignorelist.txt'), 'w') as f:
            for plate in IGNORELIST:
                f.write('%s\n' % plate)
        log(LogType.DEBUG, 'save_ignorelist', f'Saving: [{IGNORELIST}]')
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'save_ignorelist', e)
        return -1  # Error


def add_ignorelist(value: str) -> int:
    global IGNORELIST

    try:
        if value not in IGNORELIST:
            IGNORELIST.append(value)
            with open(get_work_dir('lists/ignorelist.txt'), 'w') as f:
                for plate in IGNORELIST:
                    f.write('%s\n' % plate)
            log(LogType.DEBUG, 'add_ignorelist', f'Adding plate [{value}]')
        return 0  # Ok
    except Exception as e:
        log(LogType.ERROR, 'add_ignorelist', e)
        return -1  # Error


def reset_fan_timer() -> None:
    global DEV_PARAMS

    try:
        file = get_work_dir('fan')
        if not os.path.isfile(file):
            with open(file, 'w') as f:
                f.write('')
                DEV_PARAMS.device.fanTimeConsumption = 0
    except Exception as e:
        log(LogType.ERROR, 'reset_fan_timer', e)


def save_log_messages() -> None:
    global LOG_WRITES

    try:
        buf = []
        ts = str(datetime.now().strftime('%Y-%m-%d'))
        for txt in LOG_WRITES:
            if any(s in txt for s in [';ERROR;', ';NETWORK;']):
                buf.append(txt)

        if len(buf) > 0:
            file = get_work_dir(f'logs/yolocam_err_{ts}.log')
            with open(file, 'a+') as f:
                while len(buf) > 0:
                    f.write(buf.pop(0) + '\n')

        file = get_work_dir(f'logs/yolocam_{ts}.log')
        with open(file, 'a+') as f:
            while len(LOG_WRITES) > 0:
                f.write(LOG_WRITES.pop(0) + '\n')
    except Exception as e:
        print(e)


def log(logtype: LogType, source: str, message, *args) -> None:
    global STARTED, DEV_PARAMS, LOG_WRITES, LOG_MESSAGES

    now = str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    msg = dict(id=[], time=now, type=str(logtype.name), source=source, message=str(format_exception(message)))
    txt = '%s; %s; %s; %s' % (msg['time'], msg['type'], msg['source'], msg['message'])

    LOG_WRITES.append(txt)
    for arg in args:
        LOG_WRITES.append(arg)
    LOG_MESSAGES.append(msg)
    while len(LOG_MESSAGES) > 50:
        del LOG_MESSAGES[0]

    if logtype == LogType.ERROR:
        log_color('  ERROR ', txt, Color.FAIL)
        print(txt, file=sys.stderr)
    elif logtype == LogType.WARNING:
        log_color(' WARNING', txt, Color.WARNING)
    elif logtype == LogType.NETWORK:
        log_color(' NETWORK', txt, Color.HEADER)
    elif logtype == LogType.DECISION:
        log_color('DECISION', txt, Color.GREEN)
    else:
        log_color('   OK   ', txt, Color.GREEN)

    if logtype == LogType.NETWORK:
        DEV_PARAMS.statistics.networkErrors += 1
    elif logtype == LogType.ERROR:
        DEV_PARAMS.statistics.fatalErrors += 1
        if DEV_PARAMS.statistics.fatalErrors > 25:
            log(LogType.DEBUG, 'log', 'REBOOTING...')
            # STARTED = False
            DEV_PARAMS.statistics.unexpectedReboots += 1
            DEV_PARAMS.statistics.fatalErrors = 0
            save_dev_parameters()
            save_log_messages()
            os.system('reboot')


def log_color(*args) -> None:
    print(f'{Color.ENDC}[{args[2]}{args[0]}{Color.ENDC}] ' + '%.200s' % args[1])


def remove_log_message_files(days: int) -> None:
    now = time.time()
    path = get_work_dir('logs')
    for f in os.listdir(path):
        f = os.path.join(path, f)
        if os.stat(f).st_mtime < now - (days * 86400):
            remove_file(f)


def remove_outdated_files(days: int) -> None:
    now = time.time()
    for name in ['decisions', 'ftp', 'email', 'post']:
        path = get_work_dir(name)
        for f in os.listdir(path):
            f = os.path.join(path, f)
            if os.stat(f).st_mtime < now - (days * 86400):
                remove_file(f)


def remove_excel_files(days: int) -> None:
    now = time.time()
    path = get_work_dir('excel')
    for f in os.listdir(path):
        f = os.path.join(path, f)
        if os.stat(f).st_mtime < now - (days * 86400):
            remove_file(f)


def remove_decision_recordings(days: int) -> None:
    now = time.time()
    path = get_work_dir('videos')
    for f in os.listdir(path):
        f = os.path.join(path, f)
        if os.stat(f).st_mtime < now - (days * 86400):
            remove_file(f)


def include_full_image():
    global CAM_PARAMS

    if is_numeric(CAM_PARAMS.lpr.includeFullImage):
        return True, int(CAM_PARAMS.lpr.includeFullImage)
    else:
        return False, 0


def save_decision_recording(id: str, text: str) -> None:
    global VIDEO_BUFFER, CAM_PARAMS

    def __save_video(_id: str, _text: str):
        try:
            sleep(1.0)  # Add an extra second to the video
            file = get_work_dir(f'videos/{_id}.avi')
            width, height = [CAM_PARAMS.lpr.decisionRecording.size.width, CAM_PARAMS.lpr.decisionRecording.size.height]
            if width == 0 or height == 0:
                height, width = VIDEO_BUFFER[0].shape[0:2]  # Set original video resolution, if size is 0; 0

            out = cv2.VideoWriter(file, cv2.VideoWriter_fourcc('X', 'V', 'I', 'D'), 25.0, (width, height))
            for frame in copy.deepcopy(VIDEO_BUFFER):
                if CAM_PARAMS.lpr.decisionRecording.infoText:
                    cv2.putText(frame, _text, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2, cv2.LINE_AA)
                out.write(cv2.resize(frame, (width, height)))
            out.release()
        except Exception as e:
            log(LogType.WARNING, 'save_decision_recording', e)

    thread.start_new_thread(__save_video, (id, text,))


def flush_decision() -> None:
    global DECISIONS, CAM_PARAMS

    flag = True
    while flag:
        flag = False
        for i, decision in enumerate(DECISIONS):  # Delete decisions if marked for deletion
            delete, index = [decision['delete'], decision['index']]
            if delete:
                del DECISIONS[i]  # Delete decision
                flag = True
                log(LogType.DEBUG, 'flush_decision', f'DECISION ({index}) deleted', decisions_to_str())
                break

    n = 5  # Max items in decision buffer
    if CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.API.value:
        flag = False
        if len(DECISIONS) > n:  # If decision buffer overflow
            for i, decision in enumerate(DECISIONS):
                pending, delete, index, id = [decision['pending'], decision['delete'], decision['index'], decision['id']]
                if (not pending and len(id) > 0) or (pending and i == 0):  # if decision is not pending and has been read by client
                    delete_decision(index)  # Delete decision
                    flag = True
                    break

        if len(DECISIONS) > n and not flag:
            d = DECISIONS[-1]  # Get last decision
            pending, index, id, data, = [d['pending'], d['index'], d['id'], d['data']]
            if not pending and len(id) == 0:  # If decision is not pending and has not been read by client, then save to file
                try:
                    file = get_work_dir(f'flushed/{data.id}.yof')
                    with open(file, 'wb') as f:
                        pickle.dump(d, f)  # Save decision to file
                        log(LogType.DEBUG, 'flush_decision', file)
                    delete_decision(index)  # Delete most resent decision
                except Exception as e:
                    log(LogType.ERROR, 'flush_decision', e)
    else:
        if len(DECISIONS) > n:  # Adjust decision buffer if too many items
            del DECISIONS[0]  # Delete oldest decision


def get_flushed_decision() -> None:
    files = glob.glob(get_work_dir('flushed/*.yof'))
    files.sort(key=os.path.getmtime)  # Sort flushed decisions
    if len(files) > 0:
        file = files[0]
        try:
            log(LogType.DEBUG, 'get_flushed_decision', file)
            with open(file, 'rb') as f:  # Open and append oldest decision
                decision = pickle.load(f)
            remove_file(file)
            append_decision(decision)
        except Exception as e:
            log(LogType.WARNING, 'get_flushed_decision', e)
            remove_file(file)  # File has some kind of error - delete it


def append_decision(value: dict) -> None:
    global DECISIONS, POST_BUFFER

    DECISIONS.append(value)  # Append new decision
    POST_BUFFER.clear()
    value['index'] = max(DECISIONS.copy(), key=lambda x: x['index']).get('index') + 1  # Get next index value
    log(LogType.DEBUG, 'append_decision', f'DECISION [{value["data"].plate}] ({value["index"]}) appended', decisions_to_str())


def delete_decision(index: int) -> None:
    global DECISIONS

    for decision in DECISIONS.copy():
        if decision['index'] == index:
            decision['delete'] = True  # Mark for deletion
            break


def find_decision(plate: str) -> (bool, any):
    for decision in DECISIONS.copy():  # Test if plate exists in DECISIONS
        if decision['data'].plate == plate and bool(decision['pending']):
            return True, decision
    return False, None


def finalize_decision(reading: PlateReaderResult) -> None:
    global CAM_PARAMS, DEV_PARAMS, DIRECTIONS, IGNORED, PLATES, IGNORELIST

    for re in reading.results:
        re.plate = re.plate.upper()
        ts = datetime.strptime(re.timestamp, '%Y-%m-%d %H:%M:%S.%f').timestamp()
        if re.plate not in DIRECTIONS:  # Add direction tracker for plate
            DIRECTIONS[re.plate] = dict(x=[re.box.xMin], y=[re.box.yMin], ts=[ts])
        else:
            DIRECTIONS[re.plate]['x'].append(re.box.xMin)  # Plate's x position
            DIRECTIONS[re.plate]['y'].append(re.box.yMin)  # Plate's y position
            DIRECTIONS[re.plate]['ts'].append(ts)  # Plate's timestamp

    for plate, direction in DIRECTIONS.copy().items():  # Test if plate is still visible in the camera view
        visible = False
        pending, decision = find_decision(plate)  # Find a pending decision
        for re in reading.results:
            if re.plate == plate:
                visible = True  # Plate is still visible in the camera view

        if (not visible or CAM_PARAMS.lpr.decisionModel == DecisionModel.ACCESS_CONTROL.value) and pending:
            points = []
            for i in range(len(direction['x'])):  # Build a list of points and timestamps to calculate the direction
                x, y, ts = direction['x'][i], direction['y'][i], direction['ts'][i]
                if len([x1 for (x1, y1, ts1) in points if x == x1 and y == y1]) == 0:
                    points.append(tuple((x, y, ts)))

            index, data, result = [decision['index'], decision['data'], decision['result']]
            points = list(filter(lambda item: item[2] > points[-1][2] - 30, points))  # Filter points that are > 30 seconds old
            data.direction = direction_lookup(plate, points)
            data.speed = calculate_speed(plate, points)

            if CAM_PARAMS.lpr.denyNumericDecision and plate.isnumeric():
                delete_decision(index)  # Remove decision when plate is numeric
                log(LogType.DECISION, 'finalize_decision', f'DECISION ({index}): [{plate}]. DECISION IGNORED WHEN NUMERIC')

            elif data.plate in IGNORELIST:
                delete_decision(index)  # Remove decision when plate is ignored
                log(LogType.DECISION, 'finalize_decision', f'DECISION ({index}): [{plate}]. DECISION IGNORED BY IGNORELIST')

            elif not allow_direction(data.direction):  # Test if direction is allowed
                if plate in IGNORED:
                    del IGNORED[plate]
                if plate in PLATES:
                    del PLATES[plate]
                delete_decision(index)  # Remove decision when direction is not allowed
                log(LogType.DECISION, 'finalize_decision', f'DECISION ({index}): [{plate}]. <{data.direction}> DIRECTION IS NOT ALLOWED')

            else:
                replace, candidate = find_candidate(data, CAM_PARAMS.lpr.useCandidates)
                if replace:
                    decision['data'].plate = candidate
                    log(LogType.DEBUG, 'finalize_decision', f'Using candidate [{candidate}] instead of [{plate}]')
                decision['pending'] = False  # Set decision to not pending

                if CAM_PARAMS.lpr.decisionRecording.length > 0:
                    ts = datetime.strptime(data.timestamp, '%Y-%m-%d %H:%M:%S.%f').isoformat(' ', 'seconds')
                    text = f'{data.address}: {ts}. [{data.plate}]'
                    save_decision_recording(data.id, text)  # Save decision video recording

                if CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.WEB_HOOK.value:
                    save_post_decision(data.id, data.to_json())  # Save decision for posting to webhook
                elif CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.FTP.value:
                    save_ftp_decision(data.id, data.to_json())  # Save decision for ftp upload
                elif CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.SOCKET.value:
                    save_tcp_decision(data.id, data.to_json())  # Save decision for tcp transmitting

                if CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.FILE.value:
                    save_decision(data.id, data.to_json())  # Save decision to JSON file

                if CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.EXCEL.value:
                    add_excel(data)  # Add decision to Excel buffer

                CAM_PARAMS.lpr.currentPlate = data.plate

                if DEV_PARAMS.statistics.decisions >= sys.maxsize:
                    DEV_PARAMS.statistics.decisions = 0
                DEV_PARAMS.statistics.decisions += 1

                log(LogType.DECISION, 'finalize_decision', f'DECISION ({index}): [{data.plate}]. <{data.direction}>', Pykson().to_json(result))

            del DIRECTIONS[plate]
            break


def save_decision(id: str, data: str) -> None:
    try:
        file = get_work_dir(f'decisions/{id}.yod')
        with open(file, 'w') as f:
            f.write(data)
    except Exception as e:
        log(LogType.ERROR, 'save_decision', e)


def get_decision(id: str) -> (bool, int, any):
    global DECISIONS, POST_BUFFER, CAM_PARAMS

    include, idx = include_full_image()
    for decision in DECISIONS.copy():
        if not decision['pending'] and include and idx > 0 and decision['data'].fullImage is None:
            if len(POST_BUFFER) >= idx:
                frame = cv2.cvtColor(POST_BUFFER[idx - 1], cv2.COLOR_BGR2GRAY)
                _, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, CAM_PARAMS.videoStream.compression])
                decision['data'].fullImage = b64encode(encoded.tobytes()).decode('ascii')

        elif not decision['pending']:
            if id not in decision['id']:
                return True, decision['index'], decision['data']

    get_flushed_decision()
    return False, 0, None


def ack_decision(id: str, index: int) -> bool:
    global DECISIONS

    for decision in DECISIONS.copy():
        if decision['index'] == index:
            if id not in decision['id']:
                decision['id'].append(str(id))
                log(LogType.DEBUG, 'ack_decision', f'FETCHED DECISIONS ({index}) from: {id}')
                return True
    return False


def post_system_status() -> None:
    def __post_system_status():
        global DEV_PARAMS, CAM_PARAMS

        url = CAM_PARAMS.monitor.url
        usr = CAM_PARAMS.monitor.username
        pwd = CAM_PARAMS.monitor.password
        headers = {"content-type": "application/json", "user-agent": "yolocam/1.1.0"}
        try:
            if is_url(url):
                data = SystemStatus()
                data.address = DEV_PARAMS.device.address
                data.firmware = DEV_PARAMS.device.firmware
                data.decisions = DEV_PARAMS.statistics.decisions
                data.sdkStatus = DEV_PARAMS.device.sdkStatus
                data.cpuTemperature = DEV_PARAMS.device.cpuTemperature
                data.enclosureTemperature = DEV_PARAMS.device.enclosureTemperature
                hh = divmod(DEV_PARAMS.device.fanTimeConsumption, 3600)
                mm = divmod(hh[1], 60)
                data.fanTime = f'{str(hh[0])}:{str(mm[0]).zfill(2)}'
                data.networkErrors = DEV_PARAMS.statistics.networkErrors
                data.fatalErrors = DEV_PARAMS.statistics.fatalErrors
                data.reboots = DEV_PARAMS.statistics.reboots
                data.systemRunning = DEV_PARAMS.status.running
                data.dockerRunning = DEV_PARAMS.status.dockerRunning
                data.cameraConnected = DEV_PARAMS.status.cameraConnected
                data.input1 = DEV_PARAMS.auxiliary.input1
                data.output1 = DEV_PARAMS.auxiliary.output1
                data.output2 = DEV_PARAMS.auxiliary.output2
                data.position = Position(DEV_PARAMS.auxiliary.position.x, DEV_PARAMS.auxiliary.position.y, DEV_PARAMS.auxiliary.position.z)
                response = requests.post(url=url, headers=headers, auth=requests.auth.HTTPBasicAuth(usr, pwd), data=Pykson().to_json(data), timeout=10.0, verify=False)

                if response.status_code == 200:
                    pass
                else:
                    log(LogType.WARNING, 'post_system_status', f'API: {url}, response error. ({response.status_code} - {response.reason})')
                response.close()
        except (requests.exceptions.RequestException, Exception) as e:
            log(LogType.NETWORK, 'post_system_status', f'Error: {e}. address: {url}')

    thread.start_new_thread(__post_system_status, ())


def post_decision(file: str, data: str) -> None:
    def __post_decision(_file: str, _data: str):
        global CAM_PARAMS, POST_DELAY

        url = CAM_PARAMS.lpr.deviceInterface.url
        usr = CAM_PARAMS.lpr.deviceInterface.username
        pwd = CAM_PARAMS.lpr.deviceInterface.password
        headers = {"content-type": "application/json", "user-agent": "yolocam/1.1.0"}
        POST_DELAY = 60.0

        try:
            if CAM_PARAMS.lpr.deviceInterface.authentication == AuthenticationType.BASIC.value:
                response = requests.post(url=url, data=_data, headers=headers, auth=requests.auth.HTTPBasicAuth(usr, pwd), timeout=10.0, verify=False)
            elif CAM_PARAMS.lpr.deviceInterface.authentication == AuthenticationType.DIGEST.value:
                response = requests.post(url=url, data=_data, headers=headers, auth=requests.auth.HTTPDigestAuth(usr, pwd), timeout=10.0, verify=False)
            elif CAM_PARAMS.lpr.deviceInterface.authentication == AuthenticationType.PROXY.value:
                response = requests.post(url=url, data=_data, headers=headers, auth=requests.auth.HTTPProxyAuth(usr, pwd), timeout=10.0, verify=False)
            else:
                response = requests.post(url=url, data=_data, headers=headers, timeout=10.0, verify=False)

            if response.status_code == 200:
                POST_DELAY = 2.0
                log(LogType.DEBUG, 'post_decision', f'API: {url}. ({response.status_code} - {response.reason})')
                remove_file(_file)  # Post success - delete the file
            else:
                log(LogType.WARNING, 'post_decision', f'API: {url}, response error. ({response.status_code} - {response.reason})')
            response.close()
        except (requests.exceptions.RequestException, Exception) as e:
            log(LogType.NETWORK, 'post_decision', f'Error: {e}. address: {url}')

    thread.start_new_thread(__post_decision, (file, data,))


def save_post_decision(id: str, data: str) -> None:
    try:
        file = get_work_dir(f'post/{id}.yop')
        with open(file, 'wb') as f:
            pickle.dump(data, f)  # Save decision to file
    except Exception as e:
        log(LogType.ERROR, 'save_post_decision', e)


def load_post_decision() -> None:
    files = glob.glob(get_work_dir('post/*.yop'))
    files.sort(key=os.path.getmtime)  # Sort post decision files
    now = time.time()
    if len(files) > 0:
        file = files[0]
        try:
            if now - os.stat(file).st_mtime > 5:  # File must be more than 5 seconds old
                log(LogType.DEBUG, 'load_post_decision', file)
                with open(file, 'rb') as f:  # Open oldest decision
                    data = pickle.load(f)
                post_decision(file, data)
        except Exception as e:
            log(LogType.WARNING, 'load_post_decision', e)
            remove_file(file)  # File has some kind of error - delete it


def ftp_decision(file: str) -> None:
    def __ftp_decision(_file: str):
        global CAM_PARAMS, POST_DELAY

        url = str(CAM_PARAMS.lpr.deviceInterface.url)
        usr = str(CAM_PARAMS.lpr.deviceInterface.username)
        pwd = str(CAM_PARAMS.lpr.deviceInterface.password)
        POST_DELAY = 30.0

        try:
            if CAM_PARAMS.lpr.deviceInterface.authentication == AuthenticationType.BASIC.value:
                ftp = ftplib.FTP_TLS(host=url, user=usr, passwd=pwd, acct='', timeout=10.0)
            else:
                ftp = ftplib.FTP_TLS(host=url, user='', passwd='', acct='', timeout=10.0)

            try:
                ftp.encoding = 'utf-8'
                with open(_file, 'rb') as f:  # Open oldest decision
                    response = ftp.storbinary(f'STOR {os.path.basename(_file)}', f)
                ftp.quit()
                if response.startswith('226 Transfer complete'):
                    POST_DELAY = 2.0
                    log(LogType.DEBUG, 'ftp_decision', f'Response: ({response}), address: {url}')
                    remove_file(_file)  # Ftp transfer success - delete the file
                else:
                    log(LogType.WARNING, 'ftp_decision', f'Response: ({response}), address: {url}')
            except (FileNotFoundError, Exception) as e:
                log(LogType.WARNING, 'ftp_decision', e)

        except Exception as e:
            log(LogType.NETWORK, 'ftp_decision', f'Error: {e}. address: {url}')

    thread.start_new_thread(__ftp_decision, (file,))


def save_ftp_decision(id: str, data: str) -> None:
    try:
        file = get_work_dir(f'ftp/{id}.yod')
        with open(file, 'w') as f:
            f.write(data)  # Save decision to file
    except Exception as e:
        log(LogType.ERROR, 'save_ftp_decision', e)


def load_ftp_decision() -> None:
    files = glob.glob(get_work_dir('ftp/*.yod'))
    files.sort(key=os.path.getmtime)  # Sort ftp decision files
    now = time.time()
    if len(files) > 0:
        file = files[0]
        if now - os.stat(file).st_mtime > 5:  # File must be more than 5 seconds old
            log(LogType.DEBUG, 'load_ftp_decision', file)
            ftp_decision(file)


def tcp_decision(file: str, data) -> None:
    def __tcp_decision(_file: str, _data):
        global CAM_PARAMS, POST_DELAY

        POST_DELAY = 30.0
        try:
            args = str(CAM_PARAMS.lpr.deviceInterface.url).split(':')
            if len(args) == 2:
                host = args[0]  # TCP server address
                port = args[1]  # TCP server port number
                buf = []

                # Append values from decision, specified by keys in the <options> parameter
                for arg in str(CAM_PARAMS.lpr.deviceInterface.options).split(';'):
                    arg = arg.strip()
                    if arg in _data:
                        buf.append(str(_data[arg]))
                    elif arg.upper() in ASCII:
                        buf.append(chr(ASCII[arg.upper()]))

                if len(buf) > 0:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:  # https://realpython.com/python-sockets/
                        s.connect((host, int(port)))
                        s.sendall(bytes(';'.join(buf), 'utf-8'))  # Send values separated by semicolon
                        # res = s.recv(1024)
                        s.close()
                else:
                    log(LogType.WARNING, 'tcp_decision', f'No data to send, address: {CAM_PARAMS.lpr.deviceInterface.url}')

                POST_DELAY = 0.8
                log(LogType.DEBUG, 'tcp_decision', f'Response: (Ok), address: {CAM_PARAMS.lpr.deviceInterface.url}')
                remove_file(_file)  # Tcp transmit success - delete the file

            else:
                log(LogType.WARNING, 'tcp_decision', f'Host address and port not resolved, address: {CAM_PARAMS.lpr.deviceInterface.url}')

        except (requests.exceptions.RequestException, Exception) as e:
            log(LogType.NETWORK, 'tcp_decision', f'Error: {e}. address: {CAM_PARAMS.lpr.deviceInterface.url}')

    thread.start_new_thread(__tcp_decision, (file, data,))


def save_tcp_decision(id: str, data: str) -> None:
    try:
        file = get_work_dir(f'tcp/{id}.yod')
        with open(file, 'w') as f:
            f.write(data)  # Save decision to file
    except Exception as e:
        log(LogType.ERROR, 'save_tcp_decision', e)


def load_tcp_decision() -> None:
    files = glob.glob(get_work_dir('tcp/*.yod'))
    files.sort(key=os.path.getmtime)  # Sort tcp decision files
    now = time.time()
    if len(files) > 0:
        file = files[0]
        try:
            if now - os.stat(file).st_mtime >= 0.8:  # File must be more than 0.8 seconds old
                log(LogType.DEBUG, 'load_tcp_decision', file)
                with open(file, 'r') as f:  # Open oldest decision
                    data = json.loads(f.readline())
                tcp_decision(file, data)
        except Exception as e:
            log(LogType.WARNING, 'load_tcp_decision', e)
            remove_file(file)  # File has some kind of error - delete it


def add_excel(data: Decision) -> None:
    global EXCEL_BUFFER

    if data is not None:
        EXCEL_BUFFER.append(
            [str(data.address), datetime.fromisoformat(data.timestamp).strftime('%Y-%m-%d %H:%M:%S'), str(data.plate), str(data.region['code']), str(data.direction), str(data.speed), str(data.score),
             str(data.dscore)])


def save_excel() -> None:
    def __save_excel():
        global CAM_PARAMS, EXCEL_BUFFER, EXCEL_BUSY

        try:
            if not EXCEL_BUSY and CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.EXCEL.value:
                EXCEL_BUSY = True
                option = str(CAM_PARAMS.lpr.deviceInterface.options).strip()
                if option == 'weekly':  # Save weekly as: YYYY-WEEK_NO.xlsx
                    ts = str(datetime.now().strftime('%Y-%W'))
                elif option == 'monthly':  # Save monthly as: YYYY-MM.xlsx
                    ts = str(datetime.now().strftime('%Y-%m'))
                else:  # Save daily as: YYYY-MM-DD.xlsx
                    ts = str(datetime.now().strftime('%Y-%m-%d'))

                file = get_work_dir(f'excel/{ts}.csv')
                if not os.path.isfile(file):  # .csv file do not exists - convert previous .csv files to excel
                    files = glob.glob(get_work_dir('excel/*.csv'))
                    for csv_file in files:  # List .csv files and convert to excel
                        wb = Workbook(write_only=True)  # Create new workbook
                        ws = wb.create_sheet()
                        ws.title = 'Decisions'
                        ws.append(['address', 'timestamp', 'plate', 'region', 'direction', 'speed', 'score', 'dscore'])

                        with open(csv_file, 'r') as f:  # Open .csv file
                            rows = [line.rstrip() for line in f]
                        for row in rows:
                            ws.append(row.split('|'))  # Append lines to excel sheet
                        name, ext = os.path.splitext(csv_file)  # Rename .csv to .xlsx
                        xlsx_file = f'{name}.xlsx'
                        wb.save(xlsx_file)  # Save workbook
                        add_email(xlsx_file)  # Add email notification
                        remove_file(csv_file)  # Remove .csv file

                if len(EXCEL_BUFFER) > 0:
                    with open(file, 'a') as f:  # Write excel buffer to .csv file
                        while len(EXCEL_BUFFER) > 0:
                            f.write('|'.join(EXCEL_BUFFER.pop(0)) + '\n')

                EXCEL_BUSY = False
        except Exception as e:
            EXCEL_BUSY = False
            log(LogType.ERROR, 'save_excel', e)

    thread.start_new_thread(__save_excel, ())


def add_email(attachment: str) -> None:
    global CAM_PARAMS, DEV_PARAMS

    try:
        buf = []
        for recv in str(CAM_PARAMS.lpr.deviceInterface.mailTo).strip().split(';'):
            if regx.fullmatch(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', recv):  # Test for valid email address
                buf.append(recv)

        if len(buf) > 0:
            mail = Email()
            file = get_work_dir(f'email/template.json')
            if os.path.isfile(file):  # Use SMTP host template file
                with open(file, 'r') as f:
                    mail = Pykson().from_json(f.read(), Email, accept_unknown=True)
                    mail.recipients = buf
                    mail.attachment = attachment
            else:  # Use default SMTP host
                mail.recipients = buf
                mail.attachment = attachment
            mail.body = f'This email with attachment is sent from Yolocam: {DEV_PARAMS.device.address}'

            file = get_work_dir(f'email/{str(uuid.uuid4())}.eml')
            with open(file, 'w') as f:
                f.write(Pykson().to_json(mail))
            log(LogType.DEBUG, 'add_email', file)
    except Exception as e:
        log(LogType.WARNING, 'add_email', e)


def send_email() -> None:
    def __send_email():
        try:
            files = glob.glob(get_work_dir('email/*.eml'))
            for file in files:  # List .eml files and prepare an email
                with open(file, 'r') as f:
                    mail = Pykson().from_json(f.read(), Email, accept_unknown=True)

                # Create a multipart message and set headers
                message = MIMEMultipart()
                message['From'] = mail.sender
                message['To'] = ", ".join(mail.recipients)
                message['Subject'] = mail.subject
                message.attach(MIMEText(mail.body, 'plain'))  # Add body to email

                attachment = mail.attachment
                basename = os.path.basename(attachment)

                if os.path.isfile(attachment):
                    with open(attachment, 'rb') as f:  # Open attachment in binary mode
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(f.read())
                    encoders.encode_base64(part)  # Encode file in ASCII characters to send by email

                    # Add header as key/value pair to attachment part
                    part.add_header('Content-Disposition', f'attachment; filename={basename}', )
                    # Add attachment to message and convert message to string
                    message.attach(part)
                    # Log in to server using secure context and send email
                    context = ssl.create_default_context()
                    with smtplib.SMTP_SSL(mail.host, mail.port, context=context) as server:
                        server.login(mail.username, mail.password)
                        server.sendmail(mail.sender, mail.recipients, message.as_string())
                    log(LogType.DEBUG, 'send_email', f'to: {mail.recipients}, attachment: {basename}')
                else:
                    log(LogType.WARNING, 'send_email', f'to: {mail.recipients}, attachment: {basename}, file not found')

                remove_file(file)  # Remove .eml file
        except Exception as e:
            log(LogType.WARNING, 'send_email', e)

    thread.start_new_thread(__send_email, ())


def get_log_messages(id: str) -> list:
    global LOG_MESSAGES

    buf = []
    for msg in LOG_MESSAGES.copy():
        if id not in msg['id']:
            msg['id'].append(str(id))
            buf.insert(0, '%s~%s~%s~%s' % (msg['time'], msg['type'], msg['source'], msg['message']))
    return buf


def check_bounds(result: Result) -> (BoundsType, str):
    global CAM_PARAMS

    w, h, x, y = [result.box.xMax - result.box.xMin, result.box.yMax - result.box.yMin, result.box.xMin, result.box.yMin]

    if not (w < CAM_PARAMS.lpr.maxPlateSize.width and h < CAM_PARAMS.lpr.maxPlateSize.height):
        return BoundsType.PLATE_SIZE_MAX, f'x={x}, y={y}, width={w}, height={h}'  # The license plate size is larger than maximum
    else:
        if not (w >= CAM_PARAMS.lpr.minPlateSize.width and h >= CAM_PARAMS.lpr.minPlateSize.height):
            return BoundsType.PLATE_SIZE_MIN, f'x={x}, y={y}, width={w}, height={h}'  # The license plate size is less than minimum
        else:
            if result.score < CAM_PARAMS.lpr.minTextScore:
                return BoundsType.TEXT_SCORE_LOW, f'x={x}, y={y}, score={result.score}'  # The plate reader text score is too low
            else:
                if result.dScore < CAM_PARAMS.lpr.minPlateScore:
                    return BoundsType.PLATE_SCORE_LOW, f'x={x}, y={y}, dscore={result.dScore}'  # The plate detection score is too low
                else:
                    if not (result.box.xMin > CAM_PARAMS.lpr.plateMargin.left):
                        return BoundsType.PLATE_MARGIN_LEFT, f'x={x}, y={y}, left={CAM_PARAMS.lpr.plateMargin.left}'  # The license plate is not far enough to the left of the image
                    else:
                        if not (result.box.yMin > CAM_PARAMS.lpr.plateMargin.top):
                            return BoundsType.PLATE_MARGIN_TOP, f'x={x}, y={y}, top={CAM_PARAMS.lpr.plateMargin.top}'  # The license plate is not far enough down at the top of the image
                        else:
                            if CAM_PARAMS.camera.mountingAngle == 0 or CAM_PARAMS.camera.mountingAngle == 180:
                                w, h = [CAM_PARAMS.camera.resolution.width, CAM_PARAMS.camera.resolution.height]
                            else:
                                w, h = [CAM_PARAMS.camera.resolution.height, CAM_PARAMS.camera.resolution.width]
                            if not ((w - result.box.xMax) > CAM_PARAMS.lpr.plateMargin.right):
                                return BoundsType.PLATE_MARGIN_RIGHT, f'x={x}, y={y}, right={w - result.box.xMax}'  # The license plate is too far to the right
                            else:
                                if not ((h - result.box.yMax) > CAM_PARAMS.lpr.plateMargin.bottom):
                                    return BoundsType.PLATE_MARGIN_BOTTOM, f'x={x}, y={y}, bottom={h - result.box.yMax}'  # The license plate is too far down in the image
                                else:
                                    return BoundsType.OK, ''  # Ok


def direction_lookup(plate: str, points: list) -> str:
    global CAM_PARAMS

    if len(points) < 2:
        return 'unknown'
    else:
        directions = ['up', 'left', 'left', 'left', 'down', 'right', 'right', 'right', 'up']
        scores = dict(down=0, up=0, left=0, right=0)
        buf = []
        for i in range(len(points) - 1):  # Arrange points
            p = list(points[i][:2])
            p.extend(points[i + 1][:2])
            buf.append(tuple(p))

        for x1, y1, x2, y2 in buf:  # Calculate angels from points
            deg = math.atan2(x1 - x2, y1 - y2) / math.pi * 180
            if deg < 0:
                deg = 360 + deg

            heading = directions[round(deg / 45)]  # Calculate direction
            scores[heading] += 1  # Increment score

        best = sorted(scores.items(), reverse=True, key=lambda d: d[1])  # Sort the scores
        direction = next(iter(best))[0]  # Get first direction of sorted scores

        hX = buf[0][0] > buf[-1][2]  # Heading X: left=true, right=false
        hY = buf[0][1] < buf[-1][3]  # Heading Y: down=true, up=false
        dX = dY = 0
        for i in range(len(buf)):  # Calculate pixel movement for X and Y
            dX += buf[i][2] - buf[i][0]
            dY += buf[i][3] - buf[i][1]

        w, h = [CAM_PARAMS.camera.resolution.width, CAM_PARAMS.camera.resolution.height]
        th = CAM_PARAMS.lpr.directionThreshold  # Left / right threshold percent
        mX = round((100 / w) * abs(dX))  # X movement in percent
        mY = round((100 / h) * abs(dY))  # Y movement in percent

        log(LogType.DEBUG, 'direction_lookup', f'Movement tracking [{plate}]. hX:{"left" if hX else "right"}, hY:{"down" if hY else "up"}. x:{mX}%, y:{mY}%. dX:{dX}, dY:{dY}. {str(points)}')

        if direction in ['right', 'left']:
            if hY and mY > (mX - th):
                return 'front'
            elif not hY and mY > (mX - th):
                return 'rear'
            elif hX and mX > (mY + th):
                return 'left'
            elif not hX and mX > (mY + th):
                return 'right'
            else:
                return 'both'
        elif direction == 'up':
            return 'rear'
        elif direction == 'down':
            return 'front'
        else:
            return 'unknown'


def calculate_speed(plate, points: list) -> float:
    global CAM_PARAMS

    h1 = int(CAM_PARAMS.lpr.frameHeight)  # Image height in centimeters
    h2 = int(CAM_PARAMS.camera.resolution.height)  # Image resolution height in pixels
    if h1 == 0 or h2 == 0:
        return 0.0
    elif len(points) < 2:
        return 0.0
    else:
        try:
            speed = n = 0
            for i in range(len(points) - 1):
                _, y1, ts1 = points[i]  # First point
                _, y2, ts2 = points[i + 1]  # Next point
                cm_px = float(h1 / h2)  # cm per pixel
                td_cm = float(abs(y1 - y2) * cm_px)  # Travelled distance in cm
                dt = ts2 - ts1  # Delta time in seconds
                if dt > 0.0:
                    n += 1
                    cm_sec = float(td_cm / dt)  # Centimeters per second
                    speed += float((cm_sec * 60 * 60) / 100000)

            log(LogType.DEBUG, 'calculate_speed', f'[{plate}]. speed={speed / n:.1f}')
            return round(speed / n, 1)  # Speed in km/h
        except Exception:
            return 0.0


def allow_direction(direction: str) -> bool:
    global CAM_PARAMS

    directions = {'front': 1, 'rear': 2, 'both': 3, 'unknown': 4}
    if direction in directions:
        if CAM_PARAMS.lpr.directionFilter == 1 and directions[direction] == 1:
            return True  # Front
        elif CAM_PARAMS.lpr.directionFilter == 2 and directions[direction] == 2:
            return True  # Rear
        elif CAM_PARAMS.lpr.directionFilter == 3 and directions[direction] in [1, 2, 3, 4]:
            return True  # Both and Unknown
        else:
            return False
    else:
        return False


def remove_empty_plate(reading: PlateReaderResult) -> None:
    flag = True
    while flag:
        flag = False
        for i in range(len(reading.results)):
            if reading.results[i].plate == '':  # Remove result if plate is empty
                del reading.results[i]
                flag = True
                break


def append_reading(reading: PlateReaderResult) -> None:
    global READINGS, CAM_PARAMS, PLATES, IGNORED, GPIO, NEW_PLATE

    while len(READINGS) > 120:
        del READINGS[0]

    if len(reading.results) > 0:
        READINGS.append(reading)
        for re in reading.results:
            ts = datetime.strptime(reading.timestamp, '%Y-%m-%d %H:%M:%S.%f').timestamp()
            re.timestamp = datetime.fromtimestamp(ts + (CAM_PARAMS.lpr.decisionDelay / 1000)).strftime('%Y-%m-%d %H:%M:%S.%f')
            re.plate = re.plate.upper()
            rtn, txt = check_bounds(re)
            if rtn.value == 0:
                re.passed = True
                GPIO.pulseDigital(DIO.PLATE, 0.1)  # Blink Decision LED
                if re.plate in PLATES.copy():
                    PLATES[re.plate] += 1  # Increment license plate counts
                else:
                    PLATES[re.plate] = 1  # Insert new license plate counter
                    NEW_PLATE = True
                    auxiliary_control('NEW_PLATE')

                if re.plate not in IGNORED.copy():
                    log(LogType.DEBUG, 'append_reading1', f'PLATE [{re.plate}]. BOUNDS_CHECK: {rtn.name}, SCORE={re.score}, DSCORE={re.dScore}, RECT={Rectangle(re.box)}')
            else:
                GPIO.pulseDigital(DIO.WARN, 0.1)  # Blink Bounds Error LED
                log(LogType.WARNING, 'append_reading2', f'PLATE [{re.plate}]. BOUNDS_CHECK: {rtn.name}. ({txt})')


def plate_in_readings(plate: str) -> bool:
    global READINGS

    for rd in READINGS.copy():
        for re in rd.results:
            if re.plate == plate:
                return True  # Plate still exists in READINGS

    return False


def find_candidate(data: Decision, enabled: bool) -> (bool, str):
    plate = str(data.plate)
    if not enabled:
        return False, ''
    elif plate is None:
        return False, ''
    elif len(plate) <= 2:
        return False, ''
    elif len(plate) > 8:
        return False, ''
    elif plate.isnumeric():
        return False, ''
    else:
        if plate[0:1].isnumeric() or plate[1:2].isnumeric() or not plate[2:].isnumeric():
            for candidate in data.candidates:
                cnd = str(candidate['plate'])
                if not cnd[0:1].isnumeric() and not cnd[1:2].isnumeric() and cnd[2:].isnumeric():
                    return True, cnd
            return False, ''
        else:
            return False, ''


def remove_direction_points() -> None:
    global DIRECTIONS

    for plate, points in DIRECTIONS.copy().items():
        if len(points['ts']) == 0:  # Delete item if plate has no points
            del DIRECTIONS[plate]
            break
        else:
            for i, ts in enumerate(points['ts']):
                if ts < (time.time() - 60.0):  # Delete point if it is older than current time - 60 seconds:
                    del points['x'][i]
                    del points['y'][i]
                    del points['ts'][i]
                    break


def open_camera(address: str, username: str, password: str) -> (bool, any, str):
    try:
        mode = cv2.CAP_DSHOW  # Windows DirectShow
        if platform.system() == 'Linux':
            mode = cv2.CAP_V4L  # Video for Linux

        match = regx.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', address)
        if match is None:
            id = ''
        else:
            id = str(match.group())

        if address.isnumeric():
            return True, cv2.VideoCapture(int(address), mode), str(address)
        else:
            i = address.find('@')
            if i >= 0:
                adr = f'{address[:i]}{username}:{password}{address[i:]}'
                return True, cv2.VideoCapture(str(adr)), str(id)
            else:
                return True, cv2.VideoCapture(str(address)), str(id)
    except (ValueError, Exception):
        return False, None, ''


def set_camera_parameters(cam: any) -> any:
    global CAM_PARAMS

    cam.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_PARAMS.camera.resolution.width)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_PARAMS.camera.resolution.height)
    if CAM_PARAMS.camera.exposure == 0:
        cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3.0)  # Auto exposure on
    else:
        cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)  # Auto exposure off
        cam.set(cv2.CAP_PROP_EXPOSURE, CAM_PARAMS.camera.exposure)

    cam.set(cv2.CAP_PROP_BRIGHTNESS, CAM_PARAMS.camera.brightness)
    cam.set(cv2.CAP_PROP_CONTRAST, CAM_PARAMS.camera.contrast)
    cam.set(cv2.CAP_PROP_HUE, CAM_PARAMS.camera.hue)
    cam.set(cv2.CAP_PROP_SATURATION, CAM_PARAMS.camera.saturation)
    cam.set(cv2.CAP_PROP_SHARPNESS, CAM_PARAMS.camera.sharpness)
    cam.set(cv2.CAP_PROP_GAMMA, CAM_PARAMS.camera.gamma)
    cam.set(cv2.CAP_PROP_GAIN, CAM_PARAMS.camera.gain)
    cam.set(cv2.CAP_PROP_BACKLIGHT, 0)
    cam.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
    return cam


def adjust_camera_brightness(frame, flags: list, delay: int) -> (bool, int, float):  # https://gist.github.com/kmohrf/8d4653536aaa88965a69a06b81bcb022
    image = Image.fromarray(frame)  # https://stackoverflow.com/questions/43232813/convert-opencv-image-format-to-pil-image-format
    greyscale_image = image.convert('L')
    histogram = greyscale_image.histogram()
    pixels = sum(histogram)
    brightness = scale = len(histogram)

    for i in range(0, scale):
        ratio = histogram[i] / pixels
        brightness += ratio * (-scale + i)

    le = 1.0 if brightness == 255 else round((brightness / scale), 3)  # 0 = dark, 1 = bright
    levels = [(-64, 0.97, 1.0), (-60, 0.939, 0.97), (-56, 0.909, 0.939), (-52, 0.879, 0.909), (-48, 0.848, 0.879), (-44, 0.818, 0.848), (-40, 0.788, 0.818), (-36, 0.758, 0.788),
              (-32, 0.727, 0.758), (-28, 0.697, 0.727), (-24, 0.667, 0.697), (-20, 0.636, 0.667), (-16, 0.606, 0.636), (-12, 0.576, 0.606), (-8, 0.545, 0.576), (-4, 0.515, 0.545),
              (0, 0.485, 0.515), (4, 0.455, 0.485), (8, 0.424, 0.455), (12, 0.394, 0.424), (16, 0.364, 0.394), (20, 0.333, 0.364), (24, 0.303, 0.333), (28, 0.273, 0.303),
              (32, 0.242, 0.273), (36, 0.212, 0.242), (40, 0.182, 0.212), (44, 0.152, 0.182), (48, 0.121, 0.152), (52, 0.091, 0.121), (56, 0.061, 0.091), (60, 0.03, 0.061), (64, 0.0, 0.03)]

    for br, ll, lh in levels:
        if min(ll, lh) <= le <= max(ll, lh):
            break

    if not flags[0] == br and abs(flags[1] - le) > 0.015:
        flags[0] = br  # Measured brightness
        flags[1] = le  # Measured level
        flags[2] = 0  # Delay
    else:
        flags[2] += 1

    if flags[2] == delay and not flags[3] == flags[0]:
        flags[3] = flags[0]  # Current brightness
        return True, flags[3], le
    else:
        return False, flags[3], le


def rotate_frame(frame, angle: int) -> any:
    if angle in [90, 180, 270]:
        code = {90: 0, 180: 1, 270: 2, -90: 2}
        return cv2.rotate(frame, rotateCode=code[angle])
    else:
        return frame


def crop_image(frame: any, rectangle: Rectangle, width: int, height: int) -> (bool, Rectangle, any):
    try:
        # If crop size is less than plate rectangle
        if width < rectangle.width:
            width = rectangle.width + width
        if height < rectangle.height:
            height = rectangle.height + height

        ce = [int(rectangle.width / 2), int(width / 2), int(rectangle.height / 2), int(height / 2), 0, 0]  # Center values for plate rectangle
        dx = rectangle.x + ce[0] - ce[1]  # Delta x for cropped image
        dy = rectangle.y + ce[2] - ce[3]  # Delta y for cropped image
        if dx < 0:
            ce[4] = dx
            dx = 0

        if dy < 0:
            ce[5] = dy
            dy = 0

        fh, fw = frame.shape[0:2]  # Source frame width and height
        if dx + width > fw:
            ce[4] = dx - (fw - width)
            dx = fw - width

        if dy + height > fh:
            ce[5] = dy - (fh - height)
            dy = fh - height

        cropped = frame[dy:dy + height, dx:dx + width]
        return True, Rectangle(dict(x=ce[1] - ce[0] + ce[4], y=ce[3] - ce[2] + ce[5], width=rectangle.width, height=rectangle.height)), cropped
    except (IndexError, ValueError):
        return False, Rectangle(dict(x=rectangle.x, y=rectangle.y, width=rectangle.width, height=rectangle.height)), frame


def mask_image(frame: any) -> any:
    global CAM_PARAMS

    coordinates = str(CAM_PARAMS.camera.imageMask).strip().replace(';', ',')  # 0, 0; 690, 0; 1280, 700; 1280, 960; 0, 960
    if len(coordinates.split(',')) >= 6:
        try:
            height, width = [frame.shape[0], frame.shape[1]]
            mask = np.zeros((height, width), dtype=np.uint8)
            points = np.array([np.array(coordinates.split(','), dtype=int).reshape(-1, 2)])
            mask_value = 1  # 1 channel white (can be any non-zero uint8 value)
            fill_color = 160  # Any grey tone color value to fill with
            cv2.fillPoly(mask, points, mask_value)
            select = mask != mask_value  # Select everything that is not mask_value
            frame[select] = fill_color
            return frame
        except ValueError:
            return frame
    else:
        return frame


def append_video_buffer(frame: any) -> None:
    global VIDEO_BUFFER, POST_BUFFER, CAM_PARAMS

    include, idx = include_full_image()
    if not CAM_PARAMS.lpr.decisionRecording.length == 0:
        size = 25 * CAM_PARAMS.lpr.decisionRecording.length
    elif include:
        size = 25 + abs(idx)

        if idx > 0:
            POST_BUFFER.append(frame)
            while len(POST_BUFFER) > 100:
                del POST_BUFFER[0]
        else:
            POST_BUFFER.clear()
    else:
        size = 0

    if size == 0:
        VIDEO_BUFFER.clear()
    else:
        VIDEO_BUFFER.append(frame)
        while len(VIDEO_BUFFER) > size:
            del VIDEO_BUFFER[0]


def platerecognizer_info() -> None:
    global DEV_PARAMS, SDK_ADDRESS

    try:
        DEV_PARAMS.device.sdkVersion = ''
        DEV_PARAMS.device.sdkLicense = ''
        url = f'http://{SDK_ADDRESS}/info/'
        response = requests.get(url=url)
        if response.status_code == 200:
            js = str(response.json()).replace("\'", "\"").replace("None", "null")
            info = Pykson().from_json(js, SdkInformation, accept_unknown=True)
            DEV_PARAMS.device.sdkVersion = info.version
            DEV_PARAMS.device.sdkLicense = info.licenseKey
            DEV_PARAMS.device.sdkStatus = response.reason
        else:
            DEV_PARAMS.device.sdkStatus = response.reason
    except (requests.exceptions.RequestException, Exception) as e:
        log(LogType.NETWORK, 'platerecognizer_info', e)
        DEV_PARAMS.device.sdkStatus = 'Not running'  # SDK not running


def platerecognizer_recognize(frame: any) -> (bool, int, str):
    global SDK_ADDRESS, SDK_TOKEN, CAM_PARAMS

    data = {'regions': [CAM_PARAMS.lpr.region], 'camera_id': CAM_PARAMS.camera.id}

    config = {}
    if not CAM_PARAMS.lpr.options.mode == '':
        config.update({'mode': CAM_PARAMS.lpr.options.mode})
    if not CAM_PARAMS.lpr.options.detection_rule == '':
        config.update({'detection_rule': CAM_PARAMS.lpr.options.detection_rule})
    if not CAM_PARAMS.lpr.options.detection_mode == '':
        config.update({'detection_mode': CAM_PARAMS.lpr.options.detection_mode})

    data['config'] = json.dumps(config)

    if CAM_PARAMS.lpr.options.mmc:
        data['mmc'] = True

    status = 503
    try:
        url = f'http://{SDK_ADDRESS}/alpr'
        headers = {'Authorization': f'Token {SDK_TOKEN}'}
        image = frame.tobytes()
        response = requests.post(url=url, files=dict(upload=image), data=data, headers=headers)
        status = response.status_code
        if status == 200:
            js = str(response.json()).replace("\'", "\"").replace("None", "null")
            return True, status, js
        else:
            return False, status, ''
    except (ValueError, ConnectionError, requests.exceptions.RequestException) as e:
        log(LogType.NETWORK, 'platerecognizer_recognize', e)
        return False, status, ''


def calculate_statistics() -> None:
    global FRAME_BUFFER, DEV_PARAMS, INFERENCE_BUFFER

    if len(FRAME_BUFFER) > 4:
        while len(FRAME_BUFFER) > 50:
            del FRAME_BUFFER[-1]  # Remove last frame if buffer contains more than 50 elements

        buf = []
        for i in range(2, len(FRAME_BUFFER) - 1):
            buf.append(len(FRAME_BUFFER[i]))  # Fill buffer with length on all images

        if DEV_PARAMS.statistics.minFrameSize == 0 or DEV_PARAMS.statistics.minFrameSize > np.min(buf):
            DEV_PARAMS.statistics.minFrameSize = int(np.min(buf))  # Find the minimum frame length
        if DEV_PARAMS.statistics.maxFrameSize == 0 or DEV_PARAMS.statistics.maxFrameSize < np.max(buf):
            DEV_PARAMS.statistics.maxFrameSize = int(np.max(buf))  # Find maximum frame length

        DEV_PARAMS.statistics.avgFrameSize = int(np.average(buf))  # Calculate the average frame length
    else:
        DEV_PARAMS.statistics.cameraFramesPerSecond = 0
        DEV_PARAMS.statistics.avgFrameSize = 0
        DEV_PARAMS.statistics.minFrameSize = 0
        DEV_PARAMS.statistics.maxFrameSize = 0

    if len(INFERENCE_BUFFER) > 0:
        while len(INFERENCE_BUFFER) > 30:
            del INFERENCE_BUFFER[-1]  # Remove last time measurement if buffer contains more than 30 elements

        if DEV_PARAMS.statistics.minLprTime == 0 or DEV_PARAMS.statistics.minLprTime > np.min(INFERENCE_BUFFER):
            DEV_PARAMS.statistics.minLprTime = round(np.min(INFERENCE_BUFFER), 1)  # Find minimum time
        if DEV_PARAMS.statistics.maxLprTime == 0 or DEV_PARAMS.statistics.maxLprTime < np.max(INFERENCE_BUFFER):
            DEV_PARAMS.statistics.maxLprTime = round(np.max(INFERENCE_BUFFER), 1)  # Find maximum time

        DEV_PARAMS.statistics.avgLprTime = round(np.average(INFERENCE_BUFFER), 1)  # Calculate the average time
    else:
        DEV_PARAMS.statistics.ocrFramesPerSecond = 0
        DEV_PARAMS.statistics.minLprTime = 0
        DEV_PARAMS.statistics.maxLprTime = 0
        DEV_PARAMS.statistics.avgLprTime = 0


def reset_statistics(flags: int) -> None:
    global DEV_PARAMS

    DEV_PARAMS.statistics.cameraFramesPerSecond = 0
    DEV_PARAMS.statistics.ocrFramesPerSecond = 0
    DEV_PARAMS.statistics.minFrameSize = 0
    DEV_PARAMS.statistics.maxFrameSize = 0
    DEV_PARAMS.statistics.avgFrameSize = 0
    DEV_PARAMS.statistics.minLprTime = 0
    DEV_PARAMS.statistics.maxLprTime = 0
    DEV_PARAMS.statistics.avgLprTime = 0
    DEV_PARAMS.statistics.networkErrors = 0
    DEV_PARAMS.statistics.fatalErrors = 0
    DEV_PARAMS.statistics.reboots = 0
    DEV_PARAMS.statistics.unexpectedReboots = 0
    if flags & 1:
        DEV_PARAMS.statistics.decisions = 0
    if flags & 2:
        DEV_PARAMS.device.fanTimeConsumption = 0
    log(LogType.DEBUG, 'reset_statistics', 'Reset all statistic counters')


def calibrate_position() -> None:
    global GYRO

    GYRO.calibrate()


def get_board_sensors() -> None:
    global DEV_PARAMS, BOARD

    if platform.system() == 'Linux':
        DEV_PARAMS.device.cpuTemperature = BOARD.getSystemTemperature()
        DEV_PARAMS.device.enclosureTemperature = GYRO.getTemperature()

        try:
            DEV_PARAMS.device.utcTime = str(datetime.now())[:19]  # Read UTC time
        except ValueError:
            DEV_PARAMS.device.utcTime = 'N/A'  # Value error

        try:
            d = os.popen('cat /proc/cpuinfo  | grep "name"| uniq').read()
            b = d.find('model name\t:')
            e = d.find('\n', b)
            if 0 <= b < e:
                DEV_PARAMS.device.cpuName = d[17: e]  # Read cpu name
        except ValueError:
            DEV_PARAMS.device.cpuName = 'N/A'  # Value error

        try:
            d = os.popen('lscpu | grep MHz').read()
            b = d.find('CPU MHz:')
            e = d.find('\n', b)
            if 0 <= b < e:
                DEV_PARAMS.device.cpuFrequency = int(float(d[e - 9: e]))  # Read CPU frequency
        except ValueError:
            DEV_PARAMS.device.cpuFrequency = 0  # Value error

        try:
            a = os.popen('free -m').read().split()
            if len(a) > 8 and 'used' in a:
                DEV_PARAMS.device.usedMemory = a[8] + ' MB'  # Read used memory
        except ValueError:
            DEV_PARAMS.device.usedMemory = 0  # Value error


def set_gpio(number: int, value: int) -> int:
    global CAM_PARAMS, GPIO

    if number in [1, 2] and value in [0, 1, 2]:  # value: 0=off, 1=on, 2=pulse
        if number == 1:
            if value == 2:
                GPIO.pulseDigital(DIO.OUT1, CAM_PARAMS.auxiliary.pulseLength)
            else:
                GPIO.setDigital(DIO.OUT1, value)
        elif number == 2:
            if value == 2:
                GPIO.pulseDigital(DIO.OUT2, CAM_PARAMS.auxiliary.pulseLength)
            else:
                GPIO.setDigital(DIO.OUT2, value)
        return 0  # Ok
    else:
        return -1  # Error


def get_gpio(number: int) -> int:
    global GPIO

    if number in [1]:
        value = 0
        if number == 1:
            value = GPIO.getDigital(DIO.IN1)
        return value  # Ok
    else:
        return -1  # Error


def auxiliary_control(*args) -> None:
    global DEV_PARAMS, CAM_PARAMS, GPIO, BLACKLIST, WHITELIST, NEW_PLATE

    for aux, out in [(CAM_PARAMS.auxiliary.output1, DIO.OUT1), (CAM_PARAMS.auxiliary.output2, DIO.OUT2)]:
        # Output control
        if aux == AuxiliaryOutput.NONE.value:
            GPIO.setDigital(out, 0)  # Set output off

        # Whitelist
        elif aux == AuxiliaryOutput.WHITELIST.value:
            if len(CAM_PARAMS.lpr.currentPlate) > 0:
                if CAM_PARAMS.lpr.currentPlate in WHITELIST:
                    GPIO.pulseDigital(out, CAM_PARAMS.auxiliary.pulseLength)  # Set pulse on output

        # Blacklist
        elif aux == AuxiliaryOutput.BLACKLIST.value:
            if len(CAM_PARAMS.lpr.currentPlate) > 0:
                if CAM_PARAMS.lpr.currentPlate in BLACKLIST:
                    GPIO.pulseDigital(out, CAM_PARAMS.auxiliary.pulseLength)  # Set pulse on output

        # Running
        elif aux == AuxiliaryOutput.RUNNING.value:
            if DEV_PARAMS.status.running:
                GPIO.setDigital(out, 1)  # Set output on
            else:
                GPIO.setDigital(out, 0)  # Set output off

        # New plate
        elif aux == AuxiliaryOutput.NEW_PLATE.value:
            if 'NEW_PLATE' in args:
                GPIO.pulseDigital(out, CAM_PARAMS.auxiliary.pulseLength)  # Set pulse on output

        # Position control
        elif aux == AuxiliaryOutput.POSITION_ALARM.value:
            pa = CAM_PARAMS.auxiliary.positionAlarm
            if pa > 0 and GYRO.isCalibrated:
                pos = GYRO.getPosition()
                if pos.x >= pa or pos.y >= pa or pos.z >= pa:
                    GPIO.setDigital(out, 1)  # Set output on
                else:
                    GPIO.setDigital(out, 0)  # Set output off
            else:
                GPIO.setDigital(out, 0)  # Set output off

        # External IR light control
        elif aux == AuxiliaryOutput.EXT_IR_LIGHT.value:
            if CAM_PARAMS.camera.irLightControl.mode == IrLightType.OFF.value:
                GPIO.setDigital(out, 0)  # Set output off
            elif CAM_PARAMS.camera.irLightControl.mode == IrLightType.ON.value:
                GPIO.setDigital(out, 1)  # Set output on
            elif CAM_PARAMS.camera.irLightControl.mode == IrLightType.AUTO.value:
                if CAM_PARAMS.camera.irLightControl.currentBrightness >= CAM_PARAMS.camera.irLightControl.brightnessThreshold:
                    GPIO.setDigital(out, 1)  # Set output on
                else:
                    GPIO.setDigital(out, 0)  # Set output off

    # Internal IR light control
    if CAM_PARAMS.auxiliary.output1 == AuxiliaryOutput.EXT_IR_LIGHT.value or CAM_PARAMS.auxiliary.output2 == AuxiliaryOutput.EXT_IR_LIGHT.value:
        GPIO.setDigital(DIO.IR, 0)  # IR light off
    elif CAM_PARAMS.camera.irLightControl.mode == IrLightType.OFF.value:
        GPIO.setDigital(DIO.IR, 0)  # IR light off
    elif CAM_PARAMS.camera.irLightControl.mode == IrLightType.ON.value:
        GPIO.setDigital(DIO.IR, 1)  # IR light on
    elif CAM_PARAMS.camera.irLightControl.mode == IrLightType.AUTO.value:
        if DEV_PARAMS.status.brightnessLevel >= CAM_PARAMS.camera.irLightControl.brightnessThreshold:
            GPIO.setDigital(DIO.IR, 1)  # IR light on
        else:
            GPIO.setDigital(DIO.IR, 0)  # IR light off

    # Fan control
    if 0 <= int(datetime.now().strftime('%M%S')) <= 45:
        GPIO.setDigital(DIO.FAN, 1)  # Force FAN on every hour for 45 seconds
    elif DEV_PARAMS.device.cpuTemperature >= CAM_PARAMS.auxiliary.startFan:
        GPIO.setDigital(DIO.FAN, 1)  # FAN on
    elif DEV_PARAMS.device.cpuTemperature <= (CAM_PARAMS.auxiliary.startFan - 7):
        GPIO.setDigital(DIO.FAN, 0)  # FAN off

    # Auxiliary status
    DEV_PARAMS.auxiliary.input1 = GPIO.getDigital(DIO.IN1)
    DEV_PARAMS.auxiliary.output1 = GPIO.getDigital(DIO.OUT1)
    DEV_PARAMS.auxiliary.output2 = GPIO.getDigital(DIO.OUT2)
    DEV_PARAMS.auxiliary.fan = GPIO.getDigital(DIO.FAN)
    DEV_PARAMS.auxiliary.irLight = GPIO.getDigital(DIO.IR)
    DEV_PARAMS.auxiliary.position = GYRO.getPosition()

    CAM_PARAMS.lpr.currentPlate = ''


def do_tasks() -> None:
    global INIT, STARTED, DEV_PARAMS, CAM_PARAMS, GPIO, POST_DELAY

    tmr = [0.0, 0.0, 0.0, 20.0, 0.0, 0.0, 0.0, 0.0]
    day = datetime.now().strftime('%d')
    usage = 0
    delay = time.perf_counter()
    isInit = False

    sleep(5.0)
    for pin in [DIO.RUN, DIO.PLATE, DIO.WARN]:  # Turn all LED's off
        GPIO.setDigital(pin, 0)

    log(LogType.DEBUG, 'do_tasks', 'Tasks started...')
    while STARTED:
        sleep(0.05)
        dt = time.perf_counter() - delay
        for i, _ in enumerate(tmr):
            tmr[i] += dt
        delay = time.perf_counter()

        if tmr[0] >= 0.250:  # Every 250 ms
            tmr[0] = 0.0
            GPIO.toggleDigital(DIO.RUN)  # Run LED
            auxiliary_control()

        if tmr[1] >= 1.0:  # Every 1 second
            tmr[1] = 0.0
            if GPIO.getDigital(DIO.FAN) == 1:
                DEV_PARAMS.device.fanTimeConsumption += 1  # Increment fan time consumption
            DEV_PARAMS.status.watchdog += 1  # Increment watchdog
            DEV_PARAMS.status.running = DEV_PARAMS.status.watchdog < 30
            if INIT:
                isInit = True
                stat = get_docker_status()
                DEV_PARAMS.device.dockerStatus = stat
                DEV_PARAMS.status.dockerRunning = ('Up' in stat and 'second' not in stat) or stat == 'N/A'
            elif not isInit:
                GPIO.toggleDigital(DIO.WARN)  # WARN LED blink
            get_board_sensors()  # Read board sensors
            calculate_statistics()  # Calculate statistics

        if tmr[2] >= 600.0:  # Every 10 minutes
            tmr[2] = 0.0

        if tmr[3] >= 30.0:  # Every 30. second
            tmr[3] = 0.0

            if not DEV_PARAMS.status.dockerRunning:  # Monitor that SDK usage counter always increments
                usage = DEV_PARAMS.device.sdkUsage
            elif CAM_PARAMS.lpr.options.enabled == 0:
                usage = DEV_PARAMS.device.sdkUsage
            elif not DEV_PARAMS.status.cameraConnected:
                usage = DEV_PARAMS.device.sdkUsage
            elif usage != DEV_PARAMS.device.sdkUsage:
                usage = DEV_PARAMS.device.sdkUsage
            elif DEV_PARAMS.device.sdkUsage > 0:
                log(LogType.ERROR, 'do_tasks', 'SDK usage counter is not incrementing')

            if DEV_PARAMS.status.dockerRunning and CAM_PARAMS.lpr.options.enabled == 1:
                platerecognizer_info()  # Get SDK information

            save_excel()  # Save pending decision to Excel file
            save_log_messages()  # Write log messages to file

        if tmr[4] >= 2.0:  # Every 2 second
            tmr[4] = 0.0
            flush_decision()  # Adjust decision buffer when too many entries - flush to file if overflow

        if tmr[5] >= POST_DELAY:
            if CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.WEB_HOOK.value:
                load_post_decision()  # Resend failed post decisions
            elif CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.FTP.value:
                load_ftp_decision()  # Resend failed ftp decisions
            elif CAM_PARAMS.lpr.deviceInterface.type == InterfaceType.SOCKET.value:
                load_tcp_decision()  # Resend failed tcp decisions
            tmr[5] = 0.0

        if tmr[6] >= 300.0:  # Every 5 minute
            tmr[6] = 0.0
            send_email()  # Send an email with attachment

        if tmr[7] >= 3600.0:  # Every hour
            tmr[7] = 0.0
            post_system_status()
            DEV_PARAMS.statistics.fatalErrors = 0  # Clear fatal error counter
            save_dev_parameters()

        if not day == datetime.now().strftime('%d'):  # Every day
            day = datetime.now().strftime('%d')
            remove_log_message_files(30)
            remove_outdated_files(60)
            remove_excel_files(365)
            remove_decision_recordings(CAM_PARAMS.lpr.decisionRecording.outdated)
            if CAM_PARAMS.firmware.autoUpdate:
                rtn, latest = get_firmware_version()
                if rtn:
                    if DEV_PARAMS.device.firmware == latest:
                        log(LogType.DEBUG, 'do_tasks', f'Firmware is up to date: (current V{DEV_PARAMS.device.firmware} vs. latest V{latest})')
                    else:
                        update_firmware()


def do_poll_camera() -> None:
    global STARTED, DEV_PARAMS, CAM_PARAMS, FRAME_BUFFER, TRIGGERS

    TRIGGERS[0].acquire()  # Trigger to start plate recognition
    BR_FLAGS = [0, 0, 0, 0]  # Adjust brightness flags
    while STARTED:
        rtn, cam, CAM_PARAMS.camera.id = open_camera(CAM_PARAMS.camera.address, CAM_PARAMS.camera.username, CAM_PARAMS.camera.password)
        # cam.setExceptionMode(True)
        DEV_PARAMS.status.cameraConnected = rtn and cam.isOpened()
        FRAME_BUFFER.clear()
        if not DEV_PARAMS.status.cameraConnected:
            log(LogType.NETWORK, 'do_poll_camera', f'CAMERA: [{CAM_PARAMS.camera.address}] COULD NOT CONNECT')
            cam.release()
            sleep(12.0)

        else:  # Camera is connected
            cam = set_camera_parameters(cam)
            delay1, delay2 = [time.time(), time.time()]  # Delays
            err = 0  # Camera read errors
            fps = 0  # Frames per second
            log(LogType.DEBUG, 'do_poll_camera', f'CAMERA: [address={CAM_PARAMS.camera.address} - {cam.getBackendName()}] CONNECTED')

            while STARTED:
                try:
                    if CAM_PARAMS.camera.changed:  # Set camera parameters
                        log(LogType.DEBUG, 'do_poll_camera', 'Camera parameters changed')
                        CAM_PARAMS.camera.changed = False
                        cam = set_camera_parameters(cam)

                    rtn, new_frame = cam.read()  # Read camera frame
                    if not rtn:  # Read error
                        err += 1
                        if err > 25:
                            log(LogType.NETWORK, 'do_poll_camera', f'CAMERA: [{CAM_PARAMS.camera.address}] DISCONNECTED')
                            break

                    else:  # Read success
                        err = 0
                        frame = rotate_frame(new_frame, CAM_PARAMS.camera.mountingAngle)
                        append_video_buffer(frame)

                        if CAM_PARAMS.videoStream.color == ColorType.BLACK_WHITE.value:
                            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)  # Convert image to gray scale

                        rtn, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, CAM_PARAMS.videoStream.compression])
                        if rtn:
                            FRAME_BUFFER.insert(2, encoded)  # Insert encoded frame into buffer

                            if time.time() >= delay2:  # Time is up for plate recognition
                                delay2 = time.time() + (1 / CAM_PARAMS.lpr.frameRate)
                                rtn, encoded_mask = cv2.imencode('.jpg', mask_image(frame), [cv2.IMWRITE_JPEG_QUALITY, CAM_PARAMS.videoStream.compression])
                                FRAME_BUFFER[0] = dict(id=1, image=encoded, masked_image=encoded_mask)  # Add frame to buffer position 0
                                if TRIGGERS[0].locked():
                                    TRIGGERS[0].release()  # Signal to start plate recognition

                        fps += 1  # Count camera frames per second
                        if time.time() >= (delay1 + 1):
                            delay1 = time.time()
                            DEV_PARAMS.statistics.cameraFramesPerSecond = fps
                            fps = 0

                            if int(CAM_PARAMS.camera.brightness) == 0:
                                rtn, bright, level = adjust_camera_brightness(new_frame, BR_FLAGS, 10)
                                if rtn:
                                    log(LogType.DEBUG, 'do_poll_camera', f'set brightness to {bright}')
                                    DEV_PARAMS.status.brightnessLevel = bright
                                    CAM_PARAMS.camera.irLightControl.currentBrightness = bright
                                    cam.set(cv2.CAP_PROP_BRIGHTNESS, float(bright))
                            else:
                                DEV_PARAMS.status.brightnessLevel = int(CAM_PARAMS.camera.brightness)
                                CAM_PARAMS.camera.irLightControl.currentBrightness = int(CAM_PARAMS.camera.brightness)

                except (ValueError, Exception) as e:
                    log(LogType.WARNING, 'do_poll_camera', e)
                    break

            cam.release()
    log(LogType.DEBUG, 'do_poll_camera', 'CAMERA RELEASED')


def do_process_image() -> None:
    global STARTED, DEV_PARAMS, CAM_PARAMS, FRAME_BUFFER, INFERENCE_BUFFER, TRIGGERS

    fps = 0
    delay = time.time()

    while STARTED:
        DEV_PARAMS.status.watchdog = 0

        if CAM_PARAMS.lpr.options.enabled == 0:
            sleep(5.0)
        elif CAM_PARAMS.auxiliary.input1 == AuxiliaryInput.LPR_DISABLED.value and DEV_PARAMS.auxiliary.input1 == 1:
            sleep(5.0)
        elif len(FRAME_BUFFER) <= 2:
            sleep(5.0)
        elif not DEV_PARAMS.status.dockerRunning or DEV_PARAMS.device.sdkStatus == '':
            sleep(15.0)
        elif DEV_PARAMS.device.sdkStatus != 'OK':
            log(LogType.WARNING, 'do_process_image1', DEV_PARAMS.device.sdkStatus)
            sleep(15.0)
        else:
            try:
                TRIGGERS[0].acquire()  # Block thread until a frame is present
                frame = FRAME_BUFFER[0]  # Get frame from buffer. dict(id=, timestamp=, image=, masked_image=)
                fps += 1  # Count processed frames per second
                if time.time() >= (delay + 1):
                    delay = time.time()
                    DEV_PARAMS.statistics.ocrFramesPerSecond = fps
                    fps = 0

                rtn, status, js = platerecognizer_recognize(frame['masked_image'])  # Recognize the frame
                if rtn:
                    reading = Pykson().from_json(js, PlateReaderResult, accept_unknown=True)
                    reading.frame = frame
                    INFERENCE_BUFFER.insert(0, reading.processingTime)  # inference
                    DEV_PARAMS.device.sdkUsage = reading.usage.calls

                    if reading.error is None:
                        remove_empty_plate(reading)
                        append_reading(reading)
                        finalize_decision(reading)

                    else:
                        log(LogType.WARNING, 'do_process_image2', f'Reading error: {reading.error}')
                        DEV_PARAMS.device.sdkStatus = reading.error

                else:
                    DEV_PARAMS.device.sdkStatus = f'HTTP status: {status}'

            except Exception as e:
                log(LogType.WARNING, 'do_process_image3', e)


def do_make_decision() -> None:
    global STARTED, DEV_PARAMS, CAM_PARAMS, READINGS, IGNORED, PLATES, DIRECTIONS, VIDEO_BUFFER, BOARD, GPIO, GYRO

    delay = 0.1  # 250 ms loop delay
    while STARTED:
        try:
            for plate, count in PLATES.copy().items():
                best = []
                if count >= CAM_PARAMS.lpr.minRecognitions:  # Enough license plates have been recognized
                    for rd in READINGS.copy():
                        for re in rd.results:
                            if re.plate == plate and re.passed:  # Append Result to a 'best' array for later comparison
                                re.loops += 1  # Increment loops to get more 'best' data
                                best.append(dict(image=rd.frame['image'], result=re))

                    loops = 0 if len(best) == 0 else best[-1]['result'].loops
                    if CAM_PARAMS.lpr.decisionModel == DecisionModel.ACCESS_CONTROL.value:
                        accept = len(best) >= CAM_PARAMS.lpr.minRecognitions * 2 and loops >= 1
                    else:
                        accept = len(best) >= CAM_PARAMS.lpr.minRecognitions and loops > 2
                    if accept:  # Select first, middle og last decision image
                        if CAM_PARAMS.lpr.selectedDecision == SelectedDecision.FIRST.value:
                            i = 0
                        elif CAM_PARAMS.lpr.selectedDecision == SelectedDecision.MIDDLE.value:
                            i = math.ceil((len(best) / 2))
                        else:
                            i = len(best) - 1

                        result: Result = best[i]['result']  # Set best result
                        if result.plate not in IGNORED.copy():  # The license plate should not be ignored
                            log(LogType.DEBUG, 'do_make_decision', f'decisions: {len(best)}, selected index: {i}')
                            IGNORED[result.plate] = 0

                            image = best[i]['image']  # Set decision image
                            rectangle = Rectangle(result.box)  # Set plate rectangle

                            fullImage = None
                            include, idx = include_full_image()
                            if not include:
                                pass
                            elif idx <= 0:
                                i = len(VIDEO_BUFFER) - abs(idx)
                                if len(VIDEO_BUFFER) >= i:
                                    frame = cv2.cvtColor(VIDEO_BUFFER[i - 1], cv2.COLOR_BGR2GRAY)
                                    _, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, CAM_PARAMS.videoStream.compression])
                                    fullImage = b64encode(encoded.tobytes()).decode('ascii')

                            if CAM_PARAMS.lpr.cropDecision.width > 0 and CAM_PARAMS.lpr.cropDecision.height > 0:  # Crop decision image
                                decode = cv2.imdecode(image, cv2.IMREAD_UNCHANGED)
                                rtn, rectangle, cropped = crop_image(decode, rectangle, CAM_PARAMS.lpr.cropDecision.width, CAM_PARAMS.lpr.cropDecision.height)
                                if rtn:  # Crop success
                                    rtn, image = cv2.imencode('.jpg', cropped, [cv2.IMWRITE_JPEG_QUALITY, CAM_PARAMS.videoStream.compression])
                                else:  # Cropping failed
                                    image = best[i]['image']
                                    rectangle = Rectangle(result.box)
                                    log(LogType.WARNING, 'do_make_decision', 'Cropping failed')

                            # Create new DECISION
                            decision = Decision(DEV_PARAMS.device.address, str(uuid.uuid4()), result.timestamp,
                                                result.plate, 'both', result.score, result.dScore, rectangle, 0,
                                                result.region, result.vehicle, result.candidates,
                                                b64encode(image.tobytes()).decode('ascii'), fullImage)

                            append_decision(dict(pending=True, delete=False, index=0, id=[], data=decision, result=result))  # Event based decisions
                            break

            for rd in READINGS.copy():
                for re in rd.results:
                    re.expire += delay  # Increment result expiration timer
                    if re.expire > float(CAM_PARAMS.lpr.resultExpireTime):
                        rd.results.remove(re)  # Timer have expired for max. time a Result can stay in the buffer - remove it
                        break

            for plate, expire in IGNORED.copy().items():
                if expire < CAM_PARAMS.lpr.plateBlockingTime:
                    if plate_in_readings(plate):
                        IGNORED[plate] = 0
                    else:
                        IGNORED[plate] += delay
                else:
                    if plate in IGNORED:
                        del IGNORED[plate]  # Timer have expired for ignored license plate - remove it
                    if plate in PLATES:
                        del PLATES[plate]
                    break

            remove_direction_points()  # Remove outdated direction points
            sleep(delay)  # 0.05

        except Exception as e:
            log(LogType.NETWORK, 'do_make_decision', e)

    GPIO.setDigital(DIO.IR, 0)
    GPIO.setDigital(DIO.FAN, 0)
    GPIO.setDigital(DIO.RUN, 0)
    GPIO.setDigital(DIO.PLATE, 0)
    GPIO.setDigital(DIO.WARN, 0)
    GPIO.setDigital(DIO.OUT1, 0)
    GPIO.setDigital(DIO.OUT2, 0)
    GYRO.close()
    BOARD.close()


def do_command_socket(port: int) -> None:
    async def on_connect(server, path):
        global STARTED, DEV_PARAMS, CAM_PARAMS, WATCHDOG, NEW_PLATE, READINGS, WHITELIST, BLACKLIST, IGNORELIST

        while STARTED:
            try:
                cmd = await server.recv()
                if cmd == '<PING>':  # <PING>
                    await server.send('<PING>')

                elif cmd == '<MODEL>':  # <MODEL>
                    await server.send('<MODEL:YOLOCAM>')

                elif cmd == '<WATCHDOG>':  # <WATCHDOG>
                    if WATCHDOG > 99:
                        WATCHDOG = 0
                    WATCHDOG += 1
                    await server.send(f'<WATCHDOG:{WATCHDOG}>')

                elif cmd.startswith('<GET_DEV_PARAMS>'):  # <GET_DEV_PARAMS>
                    await server.send(cmd + Pykson().to_json(DEV_PARAMS))

                elif cmd.startswith('<SET_DEV_PARAMS>'):  # <SET_DEV_PARAMS>
                    DEV_PARAMS = Pykson().from_json(cmd[16:], DeviceParameters, accept_unknown=True)
                    save_dev_parameters()
                    await server.send('<ACK>')

                elif cmd.startswith('<GET_CAM_PARAMS>'):  # <GET_CAM_PARAMS>
                    await server.send(cmd + load_cam_parameters())

                elif cmd.startswith('<SET_CAM_PARAMS>'):  # <SET_CAM_PARAMS>
                    cam = CAM_PARAMS.camera
                    CAM_PARAMS = Pykson().from_json(cmd[16:], CameraParameters, accept_unknown=True)
                    CAM_PARAMS.camera.changed = not CAM_PARAMS.camera.__eq__(cam)
                    save_cam_parameters()
                    await server.send('<ACK>')

                elif cmd.startswith('<GET_BLACKLIST>'):  # <GET_BLACKLIST>
                    load_blacklist()
                    await server.send(cmd + '|'.join(BLACKLIST))

                elif cmd.startswith('<SET_BLACKLIST>'):  # <SET_BLACKLIST>AB12345|CD67890
                    save_blacklist(cmd[15:])
                    await server.send('<ACK>')

                elif cmd.startswith('<ADD_BLACKLIST>'):  # <ADD_BLACKLIST>AB12345
                    add_blacklist(cmd[15:])
                    await server.send('<ACK>')

                elif cmd.startswith('<GET_WHITELIST>'):  # <GET_WHITELIST>
                    load_whitelist()
                    await server.send(cmd + '|'.join(WHITELIST))

                elif cmd.startswith('<SET_WHITELIST>'):  # <SET_WHITELIST>AB12345|CD67890
                    save_whitelist(cmd[15:])
                    await server.send('<ACK>')

                elif cmd.startswith('<ADD_WHITELIST>'):  # <ADD_WHITELIST>AB12345
                    add_whitelist(cmd[15:])
                    await server.send('<ACK>')

                elif cmd.startswith('<GET_IGNORELIST>'):  # <GET_IGNORELIST>
                    load_ignorelist()
                    await server.send(cmd + '|'.join(IGNORELIST))

                elif cmd.startswith('<SET_IGNORELIST>'):  # <SET_IGNORELIST>AB12345|CD67890
                    save_ignorelist(cmd[16:])
                    await server.send('<ACK>')

                elif cmd.startswith('<ADD_IGNORELIST>'):  # <ADD_IGNORELIST>AB12345
                    add_ignorelist(cmd[16:])
                    await server.send('<ACK>')

                elif cmd.startswith('<SET_GPIO:') and cmd.endswith('>'):  # '<SET_GPIO:1;0>'
                    e = cmd.find('>')
                    args = str(cmd[10:e]).split(';')
                    if len(args) == 2:
                        rtn = set_gpio(int(args[0]), int(args[1]))
                        await server.send('<NAK>' if rtn == -1 else '<SET_GPIO>')
                    else:
                        await server.send('<NUL>')

                elif cmd.startswith('<GET_GPIO:') and cmd.endswith('>'):  # '<GET_GPIO:1>'
                    e = cmd.find('>')
                    if str(cmd[10:e]).isnumeric():
                        number = int(cmd[10:e])
                        rtn = get_gpio(number)
                        await server.send('<NAK>' if rtn == -1 else f'<GET_GPIO:{rtn}>')
                    else:
                        await server.send('<NUL>')

                elif cmd.startswith('<GET_LOG_MESSAGES:') and cmd.endswith('>'):  # '<GET_LOG_MESSAGES:657c3e7b-4608-4a68-a355-bec1302eb254>'
                    e = cmd.find('>')
                    await server.send('<GET_LOG_MESSAGES>' + '|'.join(get_log_messages(cmd[18:e])))

                elif cmd.startswith('<RESET_STATISTICS:>'):  # <RESET_STATISTICS:0> bit0=Decision counter, bit1=Fan time consumption
                    e = cmd.find('>')
                    if str(cmd[18:e]).isnumeric():
                        flags = int(cmd[18:e])
                        reset_statistics(flags)
                    await server.send('<ACK>')

                elif cmd.startswith('<CALIBRATE_POSITION>'):  # <CALIBRATE_POSITION>
                    calibrate_position()
                    await server.send('<ACK>')

                elif cmd.startswith('<GET_DECISION:') and cmd.endswith('>'):  # '<GET_DECISION:657c3e7b-4608-4a68-a355-bec1302eb254>'
                    e = cmd.find('>')
                    rtn, index, decision = get_decision(cmd[14:e])
                    if rtn:
                        await server.send(f'<GET_DECISION:{index}>' + decision.to_json())
                    else:
                        await server.send('<NUL>')

                elif cmd.startswith('<ACK_DECISION:') and cmd.endswith('>'):  # '<ACK_DECISION:657c3e7b-4608-4a68-a355-bec1302eb254;12>'
                    e = cmd.find('>')
                    id, index = cmd[14:e].split(';')
                    rtn = ack_decision(id, int(index))
                    if rtn:
                        await server.send(f'<ACK_DECISION:{index}>')
                    else:
                        await server.send('<NAK>')

                elif cmd.startswith('<GET_RESULT>'):
                    if len(FRAME_BUFFER) > 2:
                        frame = FRAME_BUFFER[2]
                        rtn, status, js = platerecognizer_recognize(frame)
                        if rtn:
                            reading = Pykson().from_json(js, PlateReaderResult, accept_unknown=True)
                            value = Pykson().to_json(reading)
                            n = len(value) - 1
                            encoded = b64encode(frame.tobytes()).decode('ascii')
                            image = f', "image": "{encoded}"'
                            value = value[:n] + image + value[n:]
                            await server.send(cmd + value)
                        else:
                            await server.send('<GET_RESULT>')
                    else:
                        await server.send('<GET_RESULT>')

                elif cmd.startswith('<GET_READING:') and cmd.endswith('>'):  # '<GET_READING:AB12345>'
                    e = cmd.find('>')
                    res = '<GET_READING:>'
                    if e == -1:
                        await server.send(res)
                    else:
                        plate = str(cmd[13:e])
                        for rd in READINGS.copy():
                            for re in rd.results:
                                if re.plate == plate:
                                    value = Pykson().to_json(rd)
                                    n = len(value) - 1
                                    encoded = b64encode(rd.frame['image'].tobytes()).decode('ascii')
                                    image = f', "image": "{encoded}"'
                                    value = value[:n] + image + value[n:]
                                    res = cmd + value
                                    break
                                else:
                                    continue
                            else:
                                continue
                            break
                        await server.send(res)

                elif cmd.startswith('<GET_NEW_PLATE>'):  # <GET_NEW_PLATE>
                    value = f'<GET_NEW_PLATE:{NEW_PLATE}>'
                    NEW_PLATE = False
                    await server.send(value)

                else:
                    await server.send('<NAK>')
            except websockets.WebSocketException as e:
                await server.close()
                if e.code not in [1000, 1001]:
                    log(LogType.NETWORK, 'do_command_socket', e)
                break

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(websockets.serve(on_connect, '0.0.0.0', port))
    loop.run_forever()


def do_stream_socket(port: int) -> None:
    async def on_connect(server, path):
        global STARTED, FRAME_BUFFER

        while STARTED:
            try:
                cmd = await server.recv()
                if cmd == '<GET_FRAME>':
                    if len(FRAME_BUFFER) > 2:
                        txt = '<GET_FRAME>' + b64encode(FRAME_BUFFER[2].tobytes()).decode('ascii')
                        await server.send(txt)
                    else:
                        await server.send('<NUL>')

                else:
                    await server.send('<NAK>')
            except websockets.WebSocketException as e:
                await server.close()
                if e.code not in [1000, 1001]:
                    log(LogType.NETWORK, 'do_stream_socket', e)
                break

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(websockets.serve(on_connect, '0.0.0.0', port))
    loop.run_forever()


def do_web_socket(port: int) -> None:
    async def on_connect(server, path):
        global STARTED, DEV_PARAMS, CAM_PARAMS, FRAME_BUFFER
        # print('ws connected')
        while STARTED:
            try:
                cmd = await server.recv()
                if cmd == '<PING>':
                    await server.send('<PING>')
                elif cmd == '<GET_DEV_PARAMS>':
                    await server.send('DEV:' + Pykson().to_json(DEV_PARAMS))
                elif cmd == '<GET_CAM_PARAMS>':
                    await server.send('CAM:' + Pykson().to_json(CAM_PARAMS))
                elif cmd == '<GET_FRAME>':
                    if len(FRAME_BUFFER) > 2:
                        txt = 'FRAME:' + b64encode(FRAME_BUFFER[2].tobytes()).decode('ascii')
                        await server.send(txt)
                else:
                    await server.send('<NAK>')
            except websockets.WebSocketException as e:
                await server.close()
                if e.code not in [1000, 1001]:
                    log(LogType.NETWORK, 'do_web_socket', e)
                break

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(websockets.serve(on_connect, '0.0.0.0', port))
    # print('ws started')
    loop.run_forever()
    # print('ws stopped')


def get_docker_status() -> str:
    global STARTED

    if platform.system() == 'Windows':
        return 'N/A'

    try:
        containers = []
        for d in os.popen('docker ps -a').read().split('\n'):
            containers.append(DockerContainer(d))

        for container in containers:
            if container.image == 'platerecognizer/alpr':
                return container.status
        return 'Unknown'
    except Exception as e:
        return str(e)


def await_docker_status() -> bool:
    global STARTED, DEV_PARAMS, GPIO

    if platform.system() == 'Windows':
        return True

    stopped = 0
    action = 0
    wait = True
    while wait and STARTED:
        containers = []
        for d in os.popen('docker ps -a').read().split('\n'):
            containers.append(DockerContainer(d))

        container = None
        for container in containers:
            if container.image == 'platerecognizer/alpr':
                break

        if container is None:
            log(LogType.ERROR, 'await_docker_status', 'Docker is not installed')
            break
        elif container.status == 'STATUS':
            log(LogType.ERROR, 'await_docker_status', 'No docker image found')
            break
        elif container.image != 'platerecognizer/alpr':
            log(LogType.ERROR, 'await_docker_status', 'Docker image found is not platerecognizer')
            break
        else:
            if container.status.find('Up Less than') == 0:
                action = 1  # Started but not running - must be stopped
                stopped += 1
            elif container.status.find('Exited') == 0:
                action = 2  # Stopped - must be started
                stopped += 1
            elif 'Up' in container.status and 'second' not in container.status:
                action = 3  # Running
                stopped += 1
                if stopped >= 6:
                    wait = False
            DEV_PARAMS.device.dockerStatus = container.status

        sleep(1.0)

        if action == 3:
            log(LogType.DEBUG, 'await_docker_status', f'Docker status: {container.status}')
        else:
            log(LogType.WARNING, 'await_docker_status', f'Docker status: {container.status}')

        if wait and stopped == 8 and action == 1:
            os.popen(f'docker stop {container.container_id}')
            stopped = 0
            log(LogType.DEBUG, 'await_docker_status', 'Stop docker...')
        elif wait and stopped == 8 and action == 2:
            os.popen(f'docker start {container.container_id}')
            stopped = 0
            log(LogType.DEBUG, 'await_docker_status', 'Start docker...')

    result = action == 3 and not wait and STARTED
    GPIO.setDigital(DIO.WARN, 0 if result else 1)  # WARN LED off
    return result


def init(version: str) -> None:
    global INIT, DEV_PARAMS, TRIGGERS, BOARD, GPIO, GYRO, EXCEL_BUSY, DIO

    clear_terminal()
    create_folders()
    load_dev_parameters()
    load_cam_parameters()
    load_blacklist()
    load_whitelist()
    load_ignorelist()
    log(LogType.DEBUG, 'init', 'YOLOCAM STARTING...')
    DEV_PARAMS.device.model = 'YOLOCAM1'
    DEV_PARAMS.device.firmware = version
    CAM_PARAMS.firmware.version = version
    rtn, version = get_firmware_version()
    if rtn:
        CAM_PARAMS.firmware.latest = version
    save_cam_parameters()

    BOARD = GHF51(direction=0b00000010, negate=0b10110111)  # Direction: 0=output, 1=input
    DEV_PARAMS.device.auxiliaryEnabled = BOARD.init
    if BOARD.init:
        if BOARD.i2cProbeDevice(0x40):
            GPIO = MCP23008(board=BOARD, id=0x40, direction=0b00010000, negate=0b11010111)
            DIO = IoPin(run=0, plate=1, warn=2, fan=3, ir=5, in1=4, out1=6, out2=7)  # MCP23008 I/O pins
            log(LogType.DEBUG, 'init', 'Using MCP23008 GPIO')
        else:
            GPIO = BOARD
            DIO = IoPin(run=0, plate=2, warn=4, fan=6, ir=3, in1=1, out1=5, out2=7)  # GHF51 BOARD
            log(LogType.DEBUG, 'init', 'Using GHF51 GPIO')

        GYRO = BNO055(board=BOARD, id=0x52)
        DEV_PARAMS.device.gyroEnabled = GYRO.init
        DEV_PARAMS.device.enclosureTemperatureOption = 1 if GYRO.init else 0
        if not GYRO.init:
            log(LogType.WARNING, 'init', f'BNO055 gyroscope not initialized. (status = {GYRO.status})')
    else:
        DEV_PARAMS.device.fanTimeConsumption = 0
        log(LogType.WARNING, 'init', f'GHF51 board not initialized. (status = {to_hex(BOARD.status)})')

    # Set default I/O state
    GPIO.setDigital(DIO.IR, 0)
    GPIO.setDigital(DIO.FAN, 0)
    GPIO.setDigital(DIO.RUN, 1)
    GPIO.setDigital(DIO.PLATE, 1)
    GPIO.setDigital(DIO.WARN, 1)
    GPIO.setDigital(DIO.OUT1, 0)
    GPIO.setDigital(DIO.OUT2, 0)

    # Disable warnings from requests module
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Start socket threads
    thread.start_new_thread(do_command_socket, (10001,))  # Command port 10001
    thread.start_new_thread(do_stream_socket, (10003,))  # Streaming port 10003
    thread.start_new_thread(do_web_socket, (10005,))
    thread.start_new_thread(do_tasks, ())

    if await_docker_status():
        INIT = True
        TRIGGERS = [thread.allocate_lock(), thread.allocate_lock()]
        thread.start_new_thread(do_poll_camera, ())
        thread.start_new_thread(do_process_image, ())
        log(LogType.DEBUG, 'init', f'YOLOCAM V{DEV_PARAMS.device.firmware} STARTED')
        do_make_decision()

    while EXCEL_BUSY:
        sleep(1.0)
    log(LogType.DEBUG, 'init', 'YOLOCAM STOPPED')
    save_log_messages()
    exit(0)


if __name__ == '__main__':
    parse_arguments()
    init('1.1.0448')
