from e2e_tests.single_node_outage import (
    TestSingleNodeOutage,
    TestHASingleNodeOutage
)
from e2e_tests.single_node_failure import (
    TestSingleNodeFailure,
    TestHASingleNodeFailure
)
from e2e_tests.single_node_reboot import (
    TestSingleNodeReboot,
    TestHASingleNodeReboot
)
from e2e_tests.single_node_multi_fio_perf import (
    TestLvolFioNpcs0, TestLvolFioNpcs1, TestLvolFioNpcs2, TestLvolFioNpcsCustom
)
from e2e_tests.single_node_resize import TestSingleNodeResizeLvolCone

from e2e_tests.multi_lvol_run_fio import TestManyLvolSameNode
from e2e_tests.batch_lvol_limit import TestBatchLVOLsLimit
from e2e_tests.cloning_and_snapshot.multi_lvol_snapshot_fio import TestMultiLvolFio
from e2e_tests.ha_journal.lvol_journal_device_node_restart import TestDeviceNodeRestart
from e2e_tests.data_migration.data_migration_ha_fio import FioWorkloadTest
from e2e_tests.multi_node_crash_fio_clone import TestMultiFioSnapshotDowntime


from stress_test.lvol_stress_fio_run import TestStressLvolCloneClusterFioRun
from stress_test.lvol_ha_stress_fio import (
    TestLvolHAClusterGracefulShutdown,
    TestLvolHAClusterStorageNodeCrash,
    TestLvolHAClusterNetworkInterrupt,
    TestLvolHAClusterPartialNetworkOutage,
    TestLvolHAClusterRunAllScenarios
)
from stress_test.lvol_snap_clone_fio_failover import(
    TestFailoverScenariosStorageNodes
)
from stress_test.continuous_failover_ha import RandomFailoverTest
from stress_test.continuous_failover_ha_multi_client import RandomMultiClientFailoverTest
from stress_test.continuous_failover_ha_multi_outage import RandomMultiClientMultiFailoverTest


ALL_TESTS = [
    TestLvolFioNpcsCustom,
    TestLvolFioNpcs0,
    TestLvolFioNpcs1,
    TestLvolFioNpcs2,
    TestSingleNodeOutage,
    TestSingleNodeFailure,
    FioWorkloadTest,
    TestMultiFioSnapshotDowntime,
    TestManyLvolSameNode,
    TestBatchLVOLsLimit,
    TestMultiLvolFio,
    TestDeviceNodeRestart,
    TestHASingleNodeFailure,
    TestSingleNodeReboot,
    TestHASingleNodeReboot,
    TestHASingleNodeOutage,
    TestSingleNodeResizeLvolCone
]

def get_all_tests(custom=True, ha_test=False):
    tests = [
        TestLvolFioNpcsCustom,
        TestLvolFioNpcs0,
        TestLvolFioNpcs1,
        TestLvolFioNpcs2,
        TestSingleNodeFailure,
        TestHASingleNodeFailure,
        TestSingleNodeOutage,
        # TestSingleNodeReboot,
        # TestHASingleNodeReboot,
        TestHASingleNodeOutage,
        TestSingleNodeResizeLvolCone
        # FioWorkloadTest,
        # TestMultiFioSnapshotDowntime,
        # TestManyLvolSameNode,
        # TestBatchLVOLsLimit,

        # Enable when testing snapshot and cloning

        # TestMultiLvolFio,
        # TestSnapshotBatchCloneLVOLs,
        # TestManyClonesFromSameSnapshot,
        # TestDeviceNodeRestart
    ]
    if not custom:
        tests.remove(TestLvolFioNpcsCustom)
    else:
        tests.remove(TestLvolFioNpcs0)
        tests.remove(TestLvolFioNpcs1)
        tests.remove(TestLvolFioNpcs2)
    if not ha_test:
        tests.remove(TestHASingleNodeFailure)
        # tests.remove(TestHASingleNodeReboot)
        # tests.remove(TestHASingleNodeOutage)
    return tests

def get_stress_tests():
    tests = [
        TestStressLvolCloneClusterFioRun,
        TestLvolHAClusterGracefulShutdown,
        TestLvolHAClusterStorageNodeCrash,
        TestLvolHAClusterNetworkInterrupt,
        TestLvolHAClusterPartialNetworkOutage,
        TestLvolHAClusterRunAllScenarios,
        TestFailoverScenariosStorageNodes,
        RandomFailoverTest,
        RandomMultiClientFailoverTest,
        RandomMultiClientMultiFailoverTest,
    ]
    return tests
