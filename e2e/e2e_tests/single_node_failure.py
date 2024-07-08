### simplyblock e2e tests
import os
import time
import threading
from e2e_tests.cluster_test_base import TestClusterBase
from utils.common_utils import sleep_n_sec
from utils.sbcli_utils import SbcliUtils
from utils.ssh_utils import SshUtils
from utils.common_utils import CommonUtils
from logger_config import setup_logger
import boto3


class TestSingleNodeFailure(TestClusterBase):
    """
    Steps:
    1. Create Storage Pool and Delete Storage pool
    2. Create storage pool
    3. Create LVOL
    4. Connect LVOL
    5. Mount Device
    6. Start FIO tests
    7. While FIO is running, validate this scenario:
        a. In a cluster with three nodes, select one node, which does not
           have any lvol attached.
        b. Suspend the Node via API or CLI while the fio test is running.
        c. Shutdown the Node via API or CLI while the fio test is running.
        d. Check status of objects during outage:
            - the node is in status “offline”
            - the devices of the node are in status “unavailable”
            - lvols remain in “online” state
            - the event log contains the records indicating the object status
              changes; the event log also contains records indicating read and
              write IO errors.
            - select a cluster map from any of the two lvols (lvol get-cluster-map)
              and verify that the status changes of the node and devices are reflected in
              the other cluster map. Other two nodes and 4 devices remain online.
            - health-check status of all nodes and devices is “true”
        e. check that fio remains running without interruption.

    8. Restart the node again.
        a. check the status again:
            - the status of all nodes is “online”
            - all devices in the cluster are in status “online”
            - the event log contains the records indicating the object status changes
            - select a cluster map from any of the two lvols (lvol get-cluster-map)
              and verify that all nodes and all devices appear online
        b. check that fio remains running without interruption.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = setup_logger(__name__)
        self.ec2_client = None

    def run(self):
        """ Performs each step of the testcase
        """
        self.logger.info("Inside run function")
        initial_devices = self.ssh_obj.get_devices(node=self.mgmt_nodes[0])

        self.sbcli_utils.add_storage_pool(
            pool_name=self.pool_name
        )

        self.sbcli_utils.add_lvol(
            lvol_name=self.lvol_name,
            pool_name=self.pool_name,
            size="800M"
        )
        lvols = self.sbcli_utils.list_lvols()
        assert self.lvol_name in list(lvols.keys()), \
            f"Lvol {self.lvol_name} present in list of lvols post add: {lvols}"

        connect_str = self.sbcli_utils.get_lvol_connect_str(lvol_name=self.lvol_name)

        self.ssh_obj.exec_command(node=self.mgmt_nodes[0],
                                  command=connect_str)

        final_devices = self.ssh_obj.get_devices(node=self.mgmt_nodes[0])
        disk_use = None
        self.logger.info("Initial vs final disk:")
        self.logger.info(f"Initial: {initial_devices}")
        self.logger.info(f"Final: {final_devices}")
        for device in final_devices:
            if device not in initial_devices:
                self.logger.info(f"Using disk: /dev/{device.strip()}")
                disk_use = f"/dev/{device.strip()}"
                break
        self.ssh_obj.unmount_path(node=self.mgmt_nodes[0],
                                  device=disk_use)
        self.ssh_obj.format_disk(node=self.mgmt_nodes[0],
                                 device=disk_use)
        self.ssh_obj.mount_path(node=self.mgmt_nodes[0],
                                device=disk_use,
                                mount_path=self.mount_path)

        fio_thread1 = threading.Thread(target=self.ssh_obj.run_fio_test, args=(self.mgmt_nodes[0], None, self.mount_path, self.log_path,),
                                       kwargs={"name": "fio_run_1",
                                               "runtime": 300,
                                               "debug": self.fio_debug})
        fio_thread1.start()
        # breakpoint()

        no_lvol_node_uuid = self.sbcli_utils.get_node_without_lvols()
        no_lvol_node = self.sbcli_utils.get_storage_node_details(storage_node_id=no_lvol_node_uuid)
        instance_id = no_lvol_node[0]["ec2_instance_id"]

        self.validations(node_uuid=no_lvol_node_uuid,
                         node_status="online",
                         device_status="online",
                         lvol_status="online",
                         health_check_status=True
                         )
        
        sleep_n_sec(60)

        session = boto3.Session(
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("AWS_REGION")
        )
        self.ec2_client = session.client('ec2')
        self.logger.info(f"Instances ID:{instance_id}")
        self.logger.info(f"Region : {session.region_name}")

        self.stop_ec2_instance(instance_id)
        
        failure = None
        expected_status = "offline"
        try:
            self.logger.info(f"Waiting for node to become offline, {no_lvol_node_uuid}")
            self.sbcli_utils.wait_for_storage_node_status(no_lvol_node_uuid,
                                                          expected_status,
                                                          timeout=120)
            sleep_n_sec(20)

            self.validations(node_uuid=no_lvol_node_uuid,
                            node_status=expected_status,
                            device_status="unavailable",
                            lvol_status="online",
                            health_check_status=True)
        except (AssertionError, TimeoutError) as exp:
            self.logger.info(f"Check for expected status {expected_status} failed, "
                             "moving to other status")
            self.logger.debug(exp)
            failure = exp
        except Exception as exp:
            self.logger.debug(exp)
            self.start_ec2_instance(instance_id=instance_id)
            # self.sbcli_utils.restart_node(node_uuid=no_lvol_node_uuid)
            self.logger.info(f"Waiting for node to become online, {no_lvol_node_uuid}")
            self.sbcli_utils.wait_for_storage_node_status(no_lvol_node_uuid, "online", timeout=120)
            # sleep_n_sec(20)
            raise exp
        
        if failure:
            self.start_ec2_instance(instance_id=instance_id)
            # self.sbcli_utils.restart_node(node_uuid=no_lvol_node_uuid)
            self.logger.info(f"Waiting for node to become online, {no_lvol_node_uuid}")
            self.sbcli_utils.wait_for_storage_node_status(no_lvol_node_uuid, "online", timeout=120)
            # sleep_n_sec(20)
            raise failure
        
        self.start_ec2_instance(instance_id=instance_id)
        # self.sbcli_utils.restart_node(node_uuid=no_lvol_node_uuid)
        self.logger.info(f"Waiting for node to become online, {no_lvol_node_uuid}")
        self.sbcli_utils.wait_for_storage_node_status(no_lvol_node_uuid, "online", timeout=3*60)
        # sleep_n_sec(20)

        self.validations(node_uuid=no_lvol_node_uuid,
                         node_status="online",
                         device_status="online",
                         lvol_status="online",
                         health_check_status=True
                         )
        
        event_logs = self.sbcli_utils.get_cluster_logs(self.cluster_id)
        self.logger.info(f"Event logs: {event_logs}")

        storage_nodes = self.sbcli_utils.get_storage_nodes()["results"]
        for node in storage_nodes:
            print(f"Node {node['id']} Health: {node['health_check']}")

        # Write steps in order
        steps = {
            "Storage Node": ["shutdown", "restart"],
            "Device": {"restart"}
        }
        self.common_utils.validate_event_logs(cluster_id=self.cluster_id,
                                              operations=steps)
        
        self.common_utils.manage_fio_threads(node=self.mgmt_nodes[0],
                                             threads=[fio_thread1],
                                             timeout=1000)

        self.common_utils.validate_fio_test(node=self.mgmt_nodes[0],
                                            log_file=self.log_path)

        self.logger.info("TEST CASE PASSED !!!")

    def start_ec2_instance(self, instance_id):
        """Start ec2 instance

        Args:
            instance_id (str): Instance id to start
        """
        response = self.ec2_client.start_instances(InstanceIds=[instance_id])
        print(f'Successfully started instance {instance_id}: {response}')

        start_waiter = self.ec2_client.get_waiter('instance_running')
        self.logger.info(f"Waiting for instance {instance_id} to start...")
        start_waiter.wait(InstanceIds=[instance_id])
        self.logger.info(f'Instance {instance_id} has been successfully started.')

        sleep_n_sec(10)

    def stop_ec2_instance(self, instance_id):
        """Stop ec2 instance

        Args:
            instance_id (str): Instance id to stop
        """
        response = self.ec2_client.stop_instances(InstanceIds=[instance_id])
        self.logger.info(f'Successfully stopped instance {instance_id}: {response}')
        stop_waiter = self.ec2_client.get_waiter('instance_stopped')
        self.logger.info(f"Waiting for instance {instance_id} to stop...")
        stop_waiter.wait(InstanceIds=[instance_id])
        self.logger.info((f'Instance {instance_id} has been successfully stopped.'))
        
        sleep_n_sec(3)


    def validations(self, node_uuid, node_status, device_status, lvol_status,
                    health_check_status):
        """Validates node, devices, lvol status with expected status

        Args:
            node_uuid (str): UUID of node to validate
            node_status (str): Expected node status
            device_status (str): Expected device status
            lvol_status (str): Expected lvol status
            health_check_status (bool): Expected health check status
        """
        node_details = self.sbcli_utils.get_storage_node_details(storage_node_id=node_uuid)
        device_details = self.sbcli_utils.get_device_details(storage_node_id=node_uuid)
        lvol_id = self.sbcli_utils.get_lvol_id(lvol_name=self.lvol_name)
        lvol_details = self.sbcli_utils.get_lvol_details(lvol_id=lvol_id)
        command = f"{self.base_cmd} lvol get-cluster-map {lvol_id}"
        lvol_cluster_map_details, _ = self.ssh_obj.exec_command(node=self.mgmt_nodes[0],
                                                                command=command)
        self.logger.info(f"LVOL Cluster map: {lvol_cluster_map_details}")
        cluster_map_nodes, cluster_map_devices = self.common_utils.parse_lvol_cluster_map_output(lvol_cluster_map_details)
        offline_device = []

        assert node_details[0]["status"] == node_status, \
            f"Node {node_uuid} is not in {node_status} state. {node_details[0]['status']}"
        for device in device_details:
            # if "jm" in device["jm_bdev"]:
            #     assert device["status"] == "JM_DEV", \
            #         f"JM Device {device['id']} is not in JM_DEV state. {device['status']}"
            # else:
            assert device["status"] == device_status, \
                f"Device {device['id']} is not in {device_status} state. {device['status']}"
            offline_device.append(device['id'])

        for lvol in lvol_details:
            assert lvol["status"] == lvol_status, \
                f"Lvol {lvol['id']} is not in {lvol_status} state. {lvol['status']}"

        storage_nodes = self.sbcli_utils.get_storage_nodes()["results"]
        for node in storage_nodes:
            assert node["health_check"] == health_check_status, \
                f"Node {node['id']} health-check is not {health_check_status}. {node['health_check']}"
            device_details = self.sbcli_utils.get_device_details(storage_node_id=node["id"])
            for device in device_details:
                assert device["health_check"] == health_check_status, \
                    f"Device {device['id']} health-check is not {health_check_status}. {device['health_check']}"

        for node_id, node in cluster_map_nodes.items():
            if node_id == node_uuid:
                assert node["Reported Status"] == node_status, \
                    f"Node {node_id} is not in {node_status} state. {node['Reported Status']}"
                assert node["Actual Status"] == node_status, \
                    f"Node {node_id} is not in {node_status} state. {node['Actual Status']}"
            else:
                assert node["Reported Status"] == "online", \
                    f"Node {node_uuid} is not in online state. {node['Reported Status']}"
                assert node["Actual Status"] == "online", \
                    f"Node {node_uuid} is not in online state. {node['Actual Status']}"

        if device_status is not None:
            for device_id, device in cluster_map_devices.items():
                if device_id in offline_device:
                    assert device["Reported Status"] == device_status, \
                        f"Device {device_id} is not in {device_status} state. {device['Reported Status']}"
                    assert device["Actual Status"] == device_status, \
                        f"Device {device_id} is not in {device_status} state. {device['Actual Status']}"
                else:
                    assert device["Reported Status"] == "online", \
                        f"Device {device_id} is not in online state. {device['Reported Status']}"
                    assert device["Actual Status"] == "online", \
                        f"Device {device_id} is not in online state. {device['Actual Status']}"
