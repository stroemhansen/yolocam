import argparse
import os
import platform


class Color:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class DockerContainer:
    def __init__(self, args):
        self.container_id: str = args[0]
        self.image: str = args[1]
        self.command: str = args[2]
        self.created: str = args[3]
        self.status: str = args[4]
        if len(args) == 6:
            self.ports: str = ''
            self.names: str = args[5]
        if len(args) == 7:
            self.ports: str = args[5]
            self.names: str = args[6]

    def __str__(self):
        return '{} | {} | {} | {} | {} | {} | {}'.format(self.container_id, self.image, self.command, self.created, self.status, self.ports, self.names)


class DockerImage:
    def __init__(self, args):
        self.repository = args[0]
        self.tag = args[1]
        self.image_id = args[2]
        self.created = args[3]
        self.size = args[4]

    def __str__(self):
        return '{} | {} | {} | {} | {}'.format(self.repository, self.tag, self.image_id, self.created, self.size)


def parse_arguments():
    ap = argparse.ArgumentParser(description='Platerecognizer SDK',
                                 epilog='Start application as: python sdk.py --token <my-token> --license <my-license>')
    ap.add_argument('-t', '--token', type=str, action='store', help='SDK token.', required=True)
    ap.add_argument('-l', '--license', type=str, action='store', help='SDK license key.', required=False)

    return ap.parse_args()


def clear():
    if platform.system() == 'Linux':
        os.system('clear')


def pull_sdk_image():
    d = os.popen('docker pull platerecognizer/alpr').read()
    buf = d.splitlines()
    if d.find('Image is up to date for platerecognizer/alpr:') > 0:
        return 1, buf[-1]
    elif d.find('Downloaded newer image for platerecognizer/alpr:') > 0:
        return 0, buf[-1]
    else:
        return 2, d


def get_running_sdk():
    buf = get_all_containers()

    for container in buf:
        if container.image == 'platerecognizer/alpr' and container.status.find('Up') == 0:
            return True, container
        elif container.ports.find('8081/tcp, 0.0.0.0:8100->8080/tcp') == 0 and container.status.find('Up') == 0:
            return True, container
    return False, ''


def get_all_containers():
    buf = []
    idx = []
    for d in os.popen('docker ps -a').read().split('\n'):
        if len(idx) == 0:
            cols = ['CONTAINER ID', 'IMAGE', 'COMMAND', 'CREATED', 'STATUS', 'PORTS', 'NAMES', '.']
            d += '.'
            for i in range(len(cols) - 1):
                b, e = d.find(cols[i]), d.find(cols[i + 1])
                if 0 <= b < e:
                    idx.append((b, e - 1))

        elif len(d) > 0:
            tmp = []
            for b, e in idx:
                tmp.append(d[b:e].strip())

            if len(tmp) >= 6:
                buf.append(DockerContainer(tmp))

    return buf


def get_all_images():
    buf = []
    idx = []
    for d in os.popen('docker image list').read().split('\n'):
        if len(idx) == 0:
            cols = ['REPOSITORY', 'TAG', 'IMAGE ID', 'CREATED', 'SIZE', '.']
            d += '.'
            for i in range(len(cols) - 1):
                b, e = d.find(cols[i]), d.find(cols[i + 1])
                if 0 <= b < e:
                    idx.append((b, e - 1))

        elif len(d) > 0:
            tmp = []
            for b, e in idx:
                tmp.append(d[b:e].strip())

            if len(tmp) == 5:
                buf.append(DockerImage(tmp))

    return buf


def stop_container(id):
    d = os.popen('docker stop ' + id).read()
    if d.find(id) == 0:
        return True
    else:
        return False


def install_sdk(token, license_key):
    flag = False
    for image in get_all_images():
        if image.repository == 'platerecognizer/alpr' and image.tag == 'latest':
            flag = True
            break

    if flag:
        cmd = 'docker run -d --restart always -t -p 8100:8080 -v license:/license -e TOKEN={} -e LICENSE_KEY={} platerecognizer/alpr'.format(token, license_key)
        d = os.popen(cmd).read()
        buf = d.splitlines()
        if d.find('failed: port is already allocated') > 0:
            return False, buf[-1]
        else:
            return True, buf[-1]
    else:
        return False, 'platerecognizer/alpr image not found!'


def uninstall_sdk(token):
    cmd = 'docker run --rm -t -v license:/license -e TOKEN={} -e UNINSTALL=1 platerecognizer/alpr'.format(token)
    d = os.popen(cmd).read()
    if d.find('failed: port is already allocated') > 0:
        return False, d
    else:
        return True, d


def remove_container(id):
    d = os.popen('docker rm ' + id).read()
    if d.find(id) == 0:
        return True
    else:
        return False


def remove_image(id):
    d = os.popen('docker rmi -f ' + id).read()
    if d.find(id) == 0:
        return True
    else:
        return False


def update(token, license_key):
    print('>>> Pull latest platerecognizer/alpr SDK...')
    rtn, txt = pull_sdk_image()  # Pull latest Platerecognizer SDK image
    print('pull_sdk_image. result={}'.format(rtn))
    print(txt)
    print('-------------------------------------------------')

    if rtn == 1:
        k = input('-> SDK is up to date - proceed anyway? [y/n]: ')
        if k == 'y':
            pass
        else:
            exit(0)
    elif rtn == 0:
        pass
    else:
        exit(0)

    rtn, container = get_running_sdk()  # Get running SDK
    print('>>> Stop running container:')
    if rtn:
        print(container)
        stop_container(container.container_id)  # Stop SDK
    else:
        print(f'{Color.WARNING}No running container found{Color.ENDC}')
    print('-------------------------------------------------')

    buf = get_all_containers()  # Get all containers
    if len(buf) > 0:
        print('>>> Remove containers:')
        for container in buf:
            if container.status.find('Up') == -1 and container.status.find('Created') == -1:  # Not running
                remove_container(container.container_id)  # Remove docker container
                print(container)
        print('-------------------------------------------------')

    rtn, txt = install_sdk(token, license_key)  # Run the SDK
    if rtn:
        print('>>> Platerecognizer SDK installed')
        print(f'Result={rtn}. {txt})')
        print('-------------------------------------------------')
    else:
        print(f'{Color.WARNING}{txt}{Color.ENDC}')
        print('-------------------------------------------------')
        exit(0)

    buf = get_all_images()  # Get all images
    if len(buf) > 0:
        print('>>> Remove images:')
        for image in buf:
            if image.repository != 'platerecognizer/alpr' or (image.repository == 'platerecognizer/alpr' and image.tag == '<none>'):  # Not used
                remove_image(image.image_id)  # Remove docker image
                print(image)
        print('-------------------------------------------------')


def uninstall(token):
    rtn, container = get_running_sdk()  # Get running SDK
    print('>>> Stop running container:')
    if rtn:
        print(container)
        stop_container(container.container_id)  # Stop SDK
        print('-------------------------------------------------')
    else:
        print(f'{Color.WARNING}No running container found{Color.ENDC}')
        print('-------------------------------------------------')
        exit(0)

    rtn, txt = uninstall_sdk(token)  # Uninstall the SDK
    if rtn:
        print('>>> Platerecognizer SDK un-installed')
        print(f'Result={rtn}. {txt})')
        print('-------------------------------------------------')
    else:
        print(f'{Color.WARNING}{txt}{Color.ENDC}')
        print('-------------------------------------------------')
        exit(0)

    buf = get_all_containers()  # Get all containers
    if len(buf) > 0:
        print('>>> Remove containers:')
        for container in buf:
            if container.status.find('Up') == -1:  # Not running
                remove_container(container.container_id)  # Remove docker container
                print(container)
        print('-------------------------------------------------')

    buf = get_all_images()  # Get all images
    if len(buf) > 0:
        # print('>>> Remove images:')
        k = input('-> Remove SDK images? [y/n]: ')
        if k == 'y':
            for image in buf:
                if image.repository == 'platerecognizer/alpr' and (image.tag == 'latest' or image.tag == '<none>'):  # Not used
                    remove_image(image.image_id)  # Remove docker image
                    print(image)
        print('-------------------------------------------------')


def install(token, license_key):
    rtn, txt = install_sdk(token, license_key)  # Run the SDK
    if rtn:
        print('>>> Platerecognizer SDK installed')
        print(f'Result={rtn}. {txt})')
        print('-------------------------------------------------')
    else:
        print(f'{Color.WARNING}{txt}{Color.ENDC}')
        print('-------------------------------------------------')


if __name__ == '__main__':  # sudo chmod u+x update-sdk.sh
    clear()
    args = parse_arguments()
    print('---------------------------------------------1.2-')
    print('- [up]: Update')
    print('- [un]: Uninstall')
    print('- [in]: Install')
    print('- [x]:  Exit')
    key = input('-> Select option: ')
    print('-------------------------------------------------')
    if key == 'up':
        if args.license is None:
            key = input('-> Enter license key: ')
            print('-------------------------------------------------')
        else:
            key = args.license
        update(args.token, key)

    elif key == 'un':
        uninstall(args.token)

    elif key == 'in':
        key = args.license
        install(args.token, key)

    else:
        exit(0)








