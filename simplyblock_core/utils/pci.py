from typing import Optional
from pathlib import Path

from .helpers import ensure_singleton


def _driver_name(pci_address) -> Optional[str]:
    driver = Path(f'/sys/bus/pci/devices/{pci_address}/driver')
    return driver.readlink().name if driver.exists() else None


def device_name(pci_address) -> str:
    if _driver_name(pci_address) != 'nvme':
        raise RuntimeError('Device not bound to nvme')

    controller = ensure_singleton(Path(f'/sys/bus/pci/devices/{pci_address}/nvme').iterdir())
    return ensure_singleton(path.name for path in controller.iterdir() if path.match('nvme*n*')).name


def clear_driver(pci_address):
    driver = Path(f'/sys/bus/pci/devices/{pci_address}/driver')
    if _driver_name(pci_address) is not None:
        (driver / 'unbind').write_text(pci_address)

    driver_override = (driver / 'driver_override')
    if driver_override.exists() and driver_override.read_text() != '(null)\n':
        driver_override.write_text('\n')


def ensure_driver(pci_address, driver_name, /, set_override: bool = False):
    if _driver_name(pci_address) == driver_name:
        return

    clear_driver(pci_address)

    if set_override:
        Path(f'/sys/bus/pci/devices/{pci_address}/driver_override').write_text(f'{driver_name}\n')

    Path(f'/sys/bus/pci/drivers/{driver_name}/bind').write_text(f'{pci_address}\n')
