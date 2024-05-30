import time
import paramiko
from io import BytesIO


SSH_KEY_LOCATION = '/mnt/c/Users/Raunak Jalan/.ssh/simplyblock-us-east-2.pem'

class SshUtils:
    """Class to perform all ssh level operationa
    """

    def __init__(self, bastion_server):
        self.ssh_connections = dict()
        self.bastion_server = bastion_server

    def connect(self, address: str, port: int=22,
                bastion_server_address: str=None,
                username: str="ec2-user",
                is_bastion_server: bool=False):
        """Connect to cluster nodes

        Args:
            address (str): Address of the cluster node
            port (int): Port of the node to connect at
            bastion_server_address (str): Address of bastion server node
            username (str): Username to connect node at
            is_bastion_server (bool): Address given is of bastion server or not
        """
        # Initialize the SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Load the private key
        private_key = paramiko.Ed25519Key(filename=SSH_KEY_LOCATION)

        # Connect to the proxy server first
        if not bastion_server_address:
            bastion_server_address = self.bastion_server
        ssh.connect(hostname=bastion_server_address,
                    username=username,
                    port=port,
                    pkey=private_key,
                    timeout=3600)
        print("Connected to bastion server.")

        if self.ssh_connections.get(bastion_server_address, None):
            self.ssh_connections[bastion_server_address].close()

        self.ssh_connections[bastion_server_address] = ssh

        if is_bastion_server:
            return

        # Setup the transport to the target server through the proxy
        transport = ssh.get_transport()
        dest_addr = (address, port)
        local_addr = ('localhost', 0)
        channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)

        # Connect to the target server through the proxy channel
        target_ssh = paramiko.SSHClient()
        target_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        target_ssh.connect(address,
                           username=username,
                           port=port,
                           sock=channel,
                           pkey=private_key,
                           timeout=3600)
        print("Connected to target server through proxy.")

        # stdin, stdout, stderr = target_ssh.exec_command('sudo lsblk')
        # print("Output: ", stdout.read().decode())
        
        if self.ssh_connections.get(address, None):
            self.ssh_connections[address].close()
        self.ssh_connections[address] = target_ssh

    def exec_command(self, node, command):
        ssh_connection = self.ssh_connections[node]
        if not ssh_connection.get_transport().is_active():
            print(f"Reconnecting SSH to node {node}")
            self.connect(
                address=node,
                is_bastion_server=True if node==self.bastion_server else False
            )
            ssh_connection = self.ssh_connections[node]
        stdin, stdout, stderr = ssh_connection.exec_command(command)
        output = stdout.read().decode()
        print("Output: ", output)

        return output
    
    def exec_command_background(self, node, command, log_file=None):
        ssh_connection = self.ssh_connections[node]
        if not ssh_connection.get_transport().is_active():
            print(f"Reconnecting SSH to node {node}")
            self.connect(
                address=node,
                is_bastion_server=True if node==self.bastion_server else False
            )
            ssh_connection = self.ssh_connections[node]
        channel = ssh_connection.get_transport().open_session()
        # log_file = log_file if log_file else "/dev/null"
        channel.exec_command(f'{command} >> {log_file} &')
    
    def format_disk(self, node, device):
        """Format disk on the given node

        Args:
            node (str): Node to perform ssh operation on
            device (str): Device path
        """
        command = f"sudo mkfs.xfs {device}"
        stdout = self.exec_command(node, command)

    def mount_path(self, node, device, mount_path):
        """Mount device to given path on given node

        Args:
            node (str): Node to perform ssh operation on
            device (str): Device path
            mount_path (_type_): Mount path to perform mount on
        """
        try:
            command = f"sudo rm -rf {mount_path}"
            self.exec_command(node, command)
        except Exception as e:
            print(e)
        
        command = f"sudo mkdir {mount_path}"
        self.exec_command(node, command)

        command = f"sudo mount {device} {mount_path}"
        self.exec_command(node, command)

    def unmount_path(self, node, device):
        """Unmount device to given path on given node

        Args:
            node (str): Node to perform ssh operation on
            device (str): Device path
        """
        command = f"sudo umount {device}"
        self.exec_command(node, command)

    def get_devices(self, node):
        command = "sudo lsblk | awk '{print $1}'"
        output = self.exec_command(node, command)

        return output.strip().split("\n")[1:]
    
    def run_fio_test(self, node, device=None, directory=None, log_file=None, **kwargs):

        text = """[test]
            directory=/home/ec2-user/test_location
            ioengine=aiolib
            direct=1
            iodepth=1
            time_based=1
            runtime=3600
            readwrite=randrw
            bs=4K
            size=10MiB
            verify=md5
            numjobs=1
            verify_dump=1
            verify_fatal=0
            verify_state_save=1
            verify_backlog=10
        """

        # home_dir = self.exec_command(node, command="pwd")
        # home_dir = home_dir.strip()

        # ssh = self.ssh_connections[node]

        # ftp = ssh.open_sftp()
        # ftp.putfo(BytesIO(text.encode()), f'{home_dir}/fio_test.fio')
        # ftp.close()
        if device:
            location = f"--filename={device}"
        if directory:
            location = f"--directory={directory}"
        command = (f"sudo fio --name=test {location} --ioengine=libaio "
                   "--direct=1 --iodepth=1 --time_based --runtime=3600 --rw=randrw --bs=4K --size=10MiB "
                   "--verify=md5 --numjobs=1 --verify_dump=1 --verify_fatal=0 --verify_state_save=1 --verify_backlog=10") 
        
        print(f"COmmand: {command}")
        self.exec_command_background(node=node,
                                     command=command,
                                     log_file=log_file)
        time.sleep(10)
