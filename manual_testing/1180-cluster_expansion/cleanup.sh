#!/bin/bash

set -e

# Variables
SBCLI_CMD="sbcli"
POOL_NAME="testing1"
LVOL_SIZE="2G"
# cloning for xfs does not work well
FS_TYPES=("xfs" "ext4")
CONFIGS=("1+0" "1+1" "2+1" "4+1")
MOUNT_DIR="/mnt"
NUM_DISTRIBS=20


# Helper functions
log() {
    echo "$(date +'%Y-%m-%d %H:%M:%S') - $1"
}

get_cluster_id() {
    log "Fetching cluster ID"
    cluster_id=$($SBCLI_CMD cluster list | awk 'NR==4 {print $2}')
    log "Cluster ID: $cluster_id"
}

create_pool() {
    local cluster_id=$1
    log "Creating pool: $POOL_NAME with cluster ID: $cluster_id"
    $SBCLI_CMD pool add $POOL_NAME $cluster_id
}

connect_lvol() {
    local lvol_id=$1
    log "Connecting logical volume: $lvol_id"
    connect_command="$SBCLI_CMD -d lvol connect $lvol_id"
    log "Running connect command: $connect_command"
    eval sudo $connect_command
}

connect_lvol() {
    local lvol_id=$1
    log "Connecting logical volume: $lvol_id"
    connect_command=$($SBCLI_CMD lvol connect $lvol_id)
    log "Running connect command: $connect_command"
    eval sudo $connect_command
}

format_fs() {
    local device=$1
    local fs_type=$2
    log "Formatting device: /dev/$device with filesystem: $fs_type"
    echo "sudo mkfs.$fs_type /dev/$device"
    sudo mkfs.$fs_type /dev/$device
}

run_fio_workload() {
    local mount_point=$1
    local size=$2
    local nrfiles=$3
    log "Running fio workload on mount point: $mount_point with size: $size"
    sudo fio --directory=$mount_point --readwrite=randrw --bs=4K-128K --size=$size --name=test --numjobs=1 --nrfiles=$nrfiles --time_based=1 --runtime=36000
}

generate_checksums() {
    local files=("$@")
    for file in "${files[@]}"; do
        log "Generating checksum for file: $file"
        sudo md5sum $file
    done
}

verify_checksums() {
    local files=("$@")
    local base_checksums=()
    for file in "${files[@]}"; do
        # log "Verifying checksum for file: $file"
        checksum=$(sudo md5sum $file | awk '{print $1}')
        base_checksums+=("$checksum")
    done
    echo "${base_checksums[@]}"
}

compare_checksums() {
    local files=("$@")
    local checksums=("$@")
    for i in "${!files[@]}"; do
        file="${files[$i]}"
        checksum="${checksums[$i]}"
        current_checksum=$(sudo md5sum "$file" | awk '{print $1}')
        if [ "$current_checksum" == "$checksum" ]; then
            log "Checksum OK for $file"
        else
            log "FAIL: Checksum mismatch for $file"
            exit 1
        fi
    done
}

delete_snapshots() {
    log "Deleting all snapshots"
    snapshots=$($SBCLI_CMD snapshot list | grep -i _ss_ | awk '{print $2}')
    for snapshot in $snapshots; do
        log "Deleting snapshot: $snapshot"
        $SBCLI_CMD -d snapshot delete $snapshot
    done
}

delete_lvols() {
    log "Deleting all logical volumes, including clones"
    lvols=$($SBCLI_CMD lvol list | grep -i lvol | awk '{print $2}')
    for lvol in $lvols; do
        log "Deleting logical volume: $lvol"
        $SBCLI_CMD -d lvol delete $lvol
    done
}

delete_pool() {
    log "Deleting pool: $POOL_NAME"
    pool_id=$($SBCLI_CMD pool list | grep -i $POOL_NAME | awk '{print $2}')
    $SBCLI_CMD pool delete $pool_id || true
}

unmount_all() {
    log "Unmounting all mount points"
    mount_points=$(mount | grep /mnt | awk '{print $3}')
    for mount_point in $mount_points; do
        log "Unmounting $mount_point"
        sudo umount $mount_point
    done
}

remove_mount_dirs() {
    log "Removing all mount point directories"
    mount_dirs=$(sudo find /mnt -mindepth 1 -type d)
    for mount_dir in $mount_dirs; do
        log "Removing directory $mount_dir"
        sudo rm -rf $mount_dir
    done
}


disconnect_lvols() {
    log "Disconnecting all NVMe devices with NQN containing 'lvol'"
    subsystems=$(sudo nvme list-subsys | grep -i lvol | awk '{print $3}' | cut -d '=' -f 2)
    for subsys in $subsystems; do
        log "Disconnecting NVMe subsystem: $subsys"
        sudo nvme disconnect -n $subsys
    done
}


pause_if_interactive_mode() {
  if [[ " $* " =~ " -i " ]]; then
    echo "Press 'c' to continue"
    while true; do
      read -n 1 -s key
      if [ "$key" = "c" ]; then
        break
      fi
    done
  fi
}


# Main cleanup script
unmount_all
remove_mount_dirs
disconnect_lvols
delete_snapshots
delete_lvols
