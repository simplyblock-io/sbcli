from typing import Annotated, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from simplyblock_core.db_controller import DBController
from simplyblock_core.controllers import snapshot_controller
from simplyblock_core.models.snapshot import SnapShot as SnapshotModel

from .cluster import Cluster
from .pool import Pool
from .dtos import SnapshotDTO

api = APIRouter(prefix='/snapshots')
db = DBController()


@api.get('/', name='clusters:pools:snapshots:list')
def list(request: Request, cluster: Cluster, pool: Pool) -> List[SnapshotDTO]:
    return [
        SnapshotDTO.from_model(snapshot, request, cluster_id=cluster.get_id(), pool_id=pool.get_id())
        for snapshot
        in db.get_snapshots()
        if snapshot.pool_uuid == pool.get_id()
    ]


instance_api = APIRouter(prefix='/{snapshot_id}')


def _lookup_snapshot(snapshot_id: UUID) -> SnapshotModel:
    snapshot = db.get_snapshot_by_id(str(snapshot_id))
    if snapshot is None:
        raise HTTPException(404, 'Snapshot does not exist')
    return snapshot


Snapshot = Annotated[SnapshotModel, Depends(_lookup_snapshot)]


@instance_api.get('/', name='clusters:pools:snapshots:detail')
def get(request: Request, cluster: Cluster, pool: Pool, snapshot: Snapshot) -> SnapshotDTO:
    return SnapshotDTO.from_model(snapshot, request, cluster_id=cluster.get_id(), pool_id=pool.get_id())


@instance_api.delete('/', name='clusters:pools:snapshots:delete', status_code=204, responses={204: {"content": None}})
def delete(cluster: Cluster, pool: Pool, snapshot: Snapshot) -> Response:
    if not snapshot_controller.delete(snapshot.get_id()):
        raise ValueError('Failed to delete snapshot')

    return Response(status_code=204)
