import threading
import json
from e2e_tests.cluster_test_base import TestClusterBase
from utils.common_utils import sleep_n_sec
from logger_config import setup_logger
from datetime import datetime
import traceback
from requests.exceptions import HTTPError
import random



class FioWorkloadTest(TestClusterBase):
    """
    This test automates:
    1. Create lvols on each node, connect lvols, check devices, and mount them.
    2. Run fio workloads.
    3. Shutdown and restart nodes, remount and check fio processes.
    4. Validate migration tasks for a specific node, ensuring fio continues running on unaffected nodes.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mount_path = "/mnt"
        self.logger = setup_logger(__name__)

    def run(self):
        self.logger.info("Starting test case: FIO Workloads with lvol connections and migrations.")

        # Step 1: Create 4 lvols on each node and connect them
        lvol_fio_path = {}
        sn_lvol_data = {}

        self.sbcli_utils.add_storage_pool(
            pool_name=self.pool_name
        )

        for i in range(0, len(self.storage_nodes)):
            node_uuid = self.sbcli_utils.get_node_without_lvols()
            sn_lvol_data[node_uuid] = []
            self.logger.info(f"Creating 2 lvols on node {node_uuid}.")
            for j in range(2):
                lvol_name = f"test_lvol_{i+1}_{j+1}"
                self.sbcli_utils.add_lvol(lvol_name=lvol_name, pool_name=self.pool_name, size="10G", host_id=node_uuid)
                sn_lvol_data[node_uuid].append(lvol_name)
                lvol_fio_path[lvol_name] = {"lvol_id": self.sbcli_utils.get_lvol_id(lvol_name=lvol_name),
                                            "mount_path": None,
                                            "disk": None}
        
        trim_node = random.choice(list(sn_lvol_data.keys()))
        mount = True
        skip_mount = False

        fs = self.ssh_obj.get_mount_points(self.mgmt_nodes[0], "/mnt")
        for device in fs:
            self.ssh_obj.unmount_path(node=self.mgmt_nodes[0], device=device)

        device_count = 1
        for node, lvol_list in sn_lvol_data.items():
            # node_ip = self.get_node_ip(node)
            # Step 2: Connect lvol to the node
            for lvol in lvol_list:
                initial_devices = self.ssh_obj.get_devices(node=self.mgmt_nodes[0])
            
                connect_str = self.sbcli_utils.get_lvol_connect_str(lvol_name=lvol)
                self.ssh_obj.exec_command(self.mgmt_nodes[0], connect_str)
                sleep_n_sec(5)

                # Step 3: Check for new device after connecting the lvol
                final_devices = self.ssh_obj.get_devices(node=self.mgmt_nodes[0])
                self.logger.info(f"Initial vs final disk on node {node}:")
                self.logger.info(f"Initial: {initial_devices}")
                self.logger.info(f"Final: {final_devices}")

                # Step 4: Identify the new device and mount it (if applicable)
                fs_type = "xfs" if lvol[-1] == "1" else "ext4"
                for device in final_devices:
                    if device not in initial_devices:
                        disk_use = f"/dev/{device.strip()}"
                        lvol_fio_path[lvol]["disk"] = disk_use
                        if trim_node == node and mount:
                            skip_mount = True
                            mount  = False
                        if not skip_mount:
                            # Unmount, format, and mount the device
                            self.ssh_obj.unmount_path(node=self.mgmt_nodes[0], device=disk_use)
                            sleep_n_sec(2)
                            self.ssh_obj.format_disk(node=self.mgmt_nodes[0], device=disk_use, fs_type=fs_type)
                            sleep_n_sec(2)
                            mount_path = f"/mnt/device_{device_count}"
                            device_count += 1
                            self.ssh_obj.mount_path(node=self.mgmt_nodes[0], device=disk_use, mount_path=mount_path)
                            lvol_fio_path[lvol]["mount_path"] = mount_path
                            sleep_n_sec(2)
                            break
                        skip_mount = False

        print(f"SN List: {sn_lvol_data}")
        print(f"LVOL Mounts: {lvol_fio_path}")

        # Step 5: Run fio workloads with different configurations
        fio_threads = self.run_fio(lvol_fio_path)

        # Step 6: Continue with node shutdown, restart, and migration task validation
        affected_node = list(sn_lvol_data.keys())[0]
        self.logger.info(f"Shutting down node {affected_node}.")

        fio_process_terminated = ["fio_test_lvol_1_1", "fio_test_lvol_1_2"]

        self.shutdown_node_and_verify(affected_node, process_name=fio_process_terminated)

        sleep_n_sec(300)

        cluster_id = self.cluster_id
        self.logger.info(f"Fetching migration tasks for cluster {cluster_id}.")
        tasks = self.sbcli_utils.get_cluster_tasks(cluster_id)

        self.logger.info(f"Validating migration tasks for node {affected_node}.")
        self.validate_migration_for_node(tasks, affected_node)

        sleep_n_sec(30)

        fio_process = self.ssh_obj.find_process_name(self.mgmt_nodes[0], 'fio')
        self.logger.info(f"FIO PROCESS: {fio_process}")
        if not fio_process:
            raise RuntimeError("FIO process was interrupted on unaffected nodes.")
        for fio in fio_process_terminated:
            assert fio not in fio_process, "FIO Process running on restarted node"

        lvol_list = sn_lvol_data[affected_node]
        affected_fio = {}
        for lvol in lvol_list:
            affected_fio[lvol] = {}
            affected_fio[lvol]["mount_path"] = lvol_fio_path[lvol]["mount_path"]
            lvol_fio_path[lvol]["disk"] = self.ssh_obj.get_lvol_vs_device(node=self.mgmt_nodes[0],
                                                                         lvol_id=lvol_fio_path[lvol]["lvol_id"])
            affected_fio[lvol]["disk"] = lvol_fio_path[lvol]["disk"]
            fs_type = "xfs" if lvol[-1] == "1" else "ext4"
            if lvol_fio_path[lvol]["mount_path"]:
                fs = self.ssh_obj.get_mount_points(self.mgmt_nodes[0],
                                                   lvol_fio_path[lvol]["mount_path"])
                for device in fs:
                    self.ssh_obj.unmount_path(node=self.mgmt_nodes[0], device=device)
                self.ssh_obj.mount_path(self.mgmt_nodes[0],
                                        device=lvol_fio_path[lvol]["disk"],
                                        mount_path=lvol_fio_path[lvol]["mount_path"])
        fio_threads.extend(self.run_fio(affected_fio))

        sleep_n_sec(120)

        # # Step 7: Stop container on another node

        affected_node = list(sn_lvol_data.keys())[1]
        self.logger.info(f"Stopping docker container on node {affected_node}.")

        self.stop_container_verify(affected_node,
                                   process_name=["fio_test_lvol_2_1", "fio_test_lvol_2_2"])

        sleep_n_sec(300)

        cluster_id = self.cluster_id
        self.logger.info(f"Fetching migration tasks for cluster {cluster_id}.")
        tasks = self.sbcli_utils.get_cluster_tasks(cluster_id)

        self.logger.info(f"Validating migration tasks for node {affected_node}.")
        self.validate_migration_for_node(tasks, affected_node)

        sleep_n_sec(30)

        lvol_list = sn_lvol_data[affected_node]
        affected_fio = {}
        for lvol in lvol_list:
            affected_fio[lvol] = {}
            affected_fio[lvol]["mount_path"] = lvol_fio_path[lvol]["mount_path"]
            lvol_fio_path[lvol]["disk"] = self.ssh_obj.get_lvol_vs_device(node=self.mgmt_nodes[0],
                                                                         lvol_id=lvol_fio_path[lvol]["lvol_id"])
            affected_fio[lvol]["disk"] = lvol_fio_path[lvol]["disk"]
            fs_type = "xfs" if lvol[-1] == "1" else "ext4"
            if lvol_fio_path[lvol]["mount_path"]:
                fs = self.ssh_obj.get_mount_points(self.mgmt_nodes[0],
                                                   lvol_fio_path[lvol]["mount_path"])
                for device in fs:
                    self.ssh_obj.unmount_path(node=self.mgmt_nodes[0], device=device)
                self.ssh_obj.mount_path(self.mgmt_nodes[0],
                                        device=lvol_fio_path[lvol]["disk"],
                                        mount_path=lvol_fio_path[lvol]["mount_path"])
        fio_threads.extend(self.run_fio(affected_fio))

        # Step 8: Stop instance


        # Step 9: Add node


        # Step 10: Remove stopped instance




        self.common_utils.manage_fio_threads(node=self.mgmt_nodes[0],
                                             threads=fio_threads,
                                             timeout=5000)

        # Wait for all fio threads to finish
        for thread in fio_threads:
            thread.join()

        self.logger.info("Test completed successfully.")

    def get_node_ip(self, node_id):
        return self.sbcli_utils.get_storage_node_details(node_id)[0]["mgmt_ip"]

    def run_fio(self, lvol_fio_path):
        self.logger.info("Starting fio workloads on the logical volumes with different configurations.")
        fio_threads = []
        fio_configs = [("randrw", "4K"), ("read", "32K"), ("write", "64K"), ("trimwrite", "16K")]
        for lvol, data in lvol_fio_path.items():
            fio_run = random.choice(fio_configs)
            if data["mount_path"]:
                thread = threading.Thread(
                                target=self.ssh_obj.run_fio_test,
                                args=(self.mgmt_nodes[0], None, data["mount_path"], None),
                                kwargs={
                                    "name": f"fio_{lvol}",
                                    "rw": fio_run[0],
                                    "ioengine": "libaio",
                                    "iodepth": 64,
                                    "bs": fio_run[1],
                                    "size": "1G",
                                    "time_based": True,
                                    "runtime": 3600,
                                    "output_file": f"/home/ec2-user/{lvol}.log",
                                    "numjobs": 2,
                                    "debug": self.fio_debug
                                }
                            )
            else:
                thread = threading.Thread(
                                target=self.ssh_obj.run_fio_test,
                                args=(self.mgmt_nodes[0], lvol_fio_path[lvol]["disk"], None, None),
                                kwargs={
                                    "name": f"fio_{lvol}",
                                    "rw": fio_configs[3][0],
                                    "ioengine": "libaio",
                                    "iodepth": 64,
                                    "bs": fio_configs[3][1],
                                    "size": "1G",
                                    "time_based": True,
                                    "runtime": 3600,
                                    "output_file": f"/home/ec2-user/{lvol}.log",
                                    "nrfiles": 2,
                                    "debug": self.fio_debug
                                }
                            )
            fio_threads.append(thread)
            thread.start()
        return fio_threads

    def shutdown_node_and_verify(self, node_id, process_name):
        """Shutdown the node and ensure fio is uninterrupted."""
        fio_process = self.ssh_obj.find_process_name(self.mgmt_nodes[0], 'fio')
        self.logger.info(f"FIO PROCESS: {fio_process}")

        output = self.ssh_obj.exec_command(node=self.mgmt_nodes[0], command="sudo df -h")
        output = output[0].strip().split('\n')
        print(f"Mount paths before suspend: {output}")
        self.sbcli_utils.suspend_node(node_id)
        self.logger.info(f"Node {node_id} suspended successfully.")

        output = self.ssh_obj.exec_command(node=self.mgmt_nodes[0], command="sudo df -h")
        output = output[0].strip().split('\n')
        print(f"Mount paths after suspend: {output}")

        sleep_n_sec(30)

        fio_process = self.ssh_obj.find_process_name(self.mgmt_nodes[0], 'fio')
        self.logger.info(f"FIO PROCESS: {fio_process}")
        if not fio_process:
            raise RuntimeError("FIO process was interrupted on unaffected nodes.")
        for fio in process_name:
            assert fio not in fio_process, "FIO Process running on suspended node"
        self.logger.info("FIO process is running uninterrupted.")

        self.sbcli_utils.shutdown_node(node_id)
        self.logger.info(f"Node {node_id} shut down successfully.")

        self.sbcli_utils.wait_for_storage_node_status(node_id=node_id, status="offline",
                                                      timeout=500)

        output = self.ssh_obj.exec_command(node=self.mgmt_nodes[0], command="sudo df -h")
        output = output[0].strip().split('\n')
        print(f"Mount paths after shutdown: {output}")
        
        # Validate fio is running on other nodes
        fio_process = self.ssh_obj.find_process_name(self.mgmt_nodes[0], 'fio')
        if not fio_process:
            raise RuntimeError("FIO process was interrupted on unaffected nodes.")
        for fio in process_name:
            assert fio not in fio_process, "FIO Process running on suspended node"
        self.logger.info("FIO process is running uninterrupted.")

        sleep_n_sec(30)

        # Restart node
        self.sbcli_utils.restart_node(node_id)
        self.logger.info(f"Node {node_id} restarted successfully.")

        self.sbcli_utils.wait_for_storage_node_status(node_id=node_id, status="online",
                                                      timeout=500)

        output = self.ssh_obj.exec_command(node=self.mgmt_nodes[0], command="sudo df -h")
        output = output[0].strip().split('\n')
        print(f"Mount paths after restart: {output}")

        fio_process = self.ssh_obj.find_process_name(self.mgmt_nodes[0], 'fio')
        if not fio_process:
            raise RuntimeError("FIO process was interrupted on unaffected nodes.")
        for fio in process_name:
            assert fio not in fio_process, "FIO Process running on suspended node"
        self.logger.info("FIO process is running uninterrupted.")

    def stop_container_verify(self, node_id, process_name):
        """Shutdown the node and ensure fio is uninterrupted."""
        output = self.ssh_obj.exec_command(node=self.mgmt_nodes[0], command="sudo df -h")
        output = output[0].strip().split('\n')
        print(f"Mount paths before shutdown: {output}")
        
        node_details = self.sbcli_utils.get_storage_node_details(node_id)
        node_ip = node_details[0]["mgmt_ip"]
        self.ssh_obj.stop_spdk_process(node_ip)

        self.logger.info(f"Docker container on node {node_id} stopped successfully.")

        output = self.ssh_obj.exec_command(node=self.mgmt_nodes[0], command="sudo df -h")
        output = output[0].strip().split('\n')
        print(f"Mount paths after suspend: {output}")

        fio_process = self.ssh_obj.find_process_name(self.mgmt_nodes[0], 'fio')
        if not fio_process:
            raise RuntimeError("FIO process was interrupted on unaffected nodes.")
        for fio in process_name:
            assert fio not in fio_process, "FIO Process running on suspended node"
        self.logger.info("FIO process is running uninterrupted.")

        sleep_n_sec(400)

        # Restart node
        self.sbcli_utils.restart_node(node_id)
        self.logger.info(f"Node {node_id} restarted successfully.")

        self.sbcli_utils.wait_for_storage_node_status(node_id=node_id, status="online",
                                                      timeout=500)

        output = self.ssh_obj.exec_command(node=self.mgmt_nodes[0], command="sudo df -h")
        output = output[0].strip().split('\n')
        print(f"Mount paths after restart: {output}")

        fio_process = self.ssh_obj.find_process_name(self.mgmt_nodes[0], 'fio')
        if not fio_process:
            raise RuntimeError("FIO process was interrupted on unaffected nodes.")
        for fio in process_name:
            assert fio not in fio_process, "FIO Process running on suspended node"
        self.logger.info("FIO process is running uninterrupted.")

    def filter_migration_tasks_for_node(self, tasks, node_id):
        """
        Filters `device_migration` tasks for a specific node.

        Args:
            tasks (list): List of task dictionaries from the API response.
            node_id (str): The UUID of the node to check for migration tasks.

        Returns:
            list: List of `device_migration` tasks for the specific node.
        """
        return [task for task in tasks if task['function_name'] == 'device_migration' and task['node_id'] == node_id]

    def validate_migration_for_node(self, tasks, node_id):
        """
        Validate that all `device_migration` tasks for a specific node have completed successfully.

        Args:
            tasks (list): List of task dictionaries from the API response.
            node_id (str): The UUID of the node to check for migration tasks.

        Raises:
            RuntimeError: If any migration task failed or did not run.
        """
        print(f"Migration tasks: {tasks}")
        node_tasks = self.filter_migration_tasks_for_node(tasks, node_id)

        if not node_tasks:
            raise RuntimeError(f"No migration tasks found for node {node_id}.")
        print(f"node tasks: {node_tasks}")
        for task in node_tasks:
            if task['status'] != 'done' or task['function_result'] != 'Done':
                raise RuntimeError(f"Migration task {task['id']} on node {node_id} failed or is incomplete.")
        
        self.logger.info(f"All migration tasks for node {node_id} completed successfully.")