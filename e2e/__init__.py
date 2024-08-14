from e2e_tests.single_node_outage import TestSingleNodeOutage
from e2e_tests.single_node_failure import TestSingleNodeFailure
from e2e_tests.single_node_multi_fio_perf import (
    TestSingleNodeMultipleFioPerfValidation,
)
from e2e_tests.multi_lvol_run_fio import TestMultiLVOLRunFIO
from e2e_tests.cloning_and_snapshot.multi_lvol_snapshot_fio import TestMultiLvolFio
from e2e_tests.cloning_and_snapshot.single_lvol_multi_clone import TestSingleLvolMultipleCloneRunFIO



def get_all_tests():
    tests = [
        TestSingleNodeOutage,
        TestSingleNodeMultipleFioPerfValidation,
        # TestSingleNodeFailure,
        # TestMultiLvolFio, - Enable when testing snapshot and cloning
        # TestMultiLVOLRunFIO, - Enable when testing snapshot and cloning
        # TestSingleLvolMultipleCloneRunFIO, - Enable when testing snapshot and cloning
    ]
    return tests