import os
from utils.sbcli_utils import SbcliUtils
from utils.ssh_utils import SshUtils
from utils.common_utils import CommonUtils
from logger_config import setup_logger


class TestClusterBase:
    def __init__(self, **kwargs):
        
        self.cluster_secret = os.environ.get("CLUSTER_SECRET")
        self.cluster_id = os.environ.get("CLUSTER_ID")

        self.api_base_url = os.environ.get("API_BASE_URL")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"{self.cluster_id} {self.cluster_secret}"
        }
        self.bastion_server = os.environ.get("BASTION_SERVER")

        self.ssh_obj = SshUtils(bastion_server=self.bastion_server)
        self.logger = setup_logger(__name__)
        self.sbcli_utils = SbcliUtils(
            cluster_api_url=self.api_base_url,
            cluster_id=self.cluster_id,
            cluster_secret=self.cluster_secret
        )
        self.common_utils = CommonUtils(self.sbcli_utils, self.ssh_obj)
        self.mgmt_nodes = None
        self.storage_nodes = None
        self.pool_name = "test_pool"
        self.lvol_name = "test_lvol"
        self.mount_path = "/home/ec2-user/test_location"
        self.log_path = f"{os.path.dirname(self.mount_path)}/log_file.log"
        self.base_cmd = os.environ.get("SBCLI_CMD", "sbcli-dev")
        self.fio_debug = kwargs.get("fio_debug", False)

    def setup(self):
        """Contains setup required to run the test case
        """
        self.logger.info("Inside setup function")
        self.mgmt_nodes, self.storage_nodes = self.sbcli_utils.get_all_nodes_ip()
        for node in self.mgmt_nodes:
            self.logger.info(f"**Connecting to management nodes** - {node}")
            self.ssh_obj.connect(
                address=node,
                bastion_server_address=self.bastion_server,
            )
        for node in self.storage_nodes:
            self.logger.info(f"**Connecting to storage nodes** - {node}")
            self.ssh_obj.connect(
                address=node,
                bastion_server_address=self.bastion_server,
            )
        self.ssh_obj.unmount_path(node=self.mgmt_nodes[0],
                                  device=self.mount_path)
        self.sbcli_utils.delete_all_lvols()
        self.sbcli_utils.delete_all_storage_pools()

    def teardown(self):
        """Contains teradown required post test case execution
        """
        self.logger.info("Inside teardown function")
        self.ssh_obj.kill_processes(node=self.mgmt_nodes[0],
                                    process_name="fio")
        lvol_id = self.sbcli_utils.get_lvol_id(lvol_name=self.lvol_name)
        if lvol_id is not None:
            lvol_details = self.sbcli_utils.get_lvol_details(lvol_id=lvol_id)
            nqn = lvol_details[0]["nqn"]
            self.ssh_obj.unmount_path(node=self.mgmt_nodes[0],
                                      device=self.mount_path)
            self.sbcli_utils.delete_all_lvols()
            self.sbcli_utils.delete_all_storage_pools()
            self.ssh_obj.exec_command(node=self.mgmt_nodes[0],
                                      command=f"sudo nvme disconnect -n {nqn}")
        for node, ssh in self.ssh_obj.ssh_connections.items():
            self.logger.info(f"Closing node ssh connection for {node}")
            ssh.close()
