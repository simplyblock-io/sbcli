import logging
from pathlib import Path
from typing import Optional

from .helpers import ensure_singleton


def _driver_name(pci_address) -> Optional[str]:
    driver = Path(f'/sys/bus/pci/devices/{pci_address}/driver')
    result = driver.readlink().name if driver.exists() else None
    logging.debug(f'{pci_address} uses {result}')
    return result


def device_name(pci_address) -> str:
    if _driver_name(pci_address) != 'nvme':
        raise RuntimeError('Device not bound to nvme')

    controller = ensure_singleton(Path(f'/sys/bus/pci/devices/{pci_address}/nvme').iterdir())
    result = ensure_singleton(path.name for path in controller.iterdir() if path.match('nvme*n*')).name
    logging.debug(f'{pci_address} device name: {result}')
    return result


def clear_driver(pci_address):
    driver = Path(f'/sys/bus/pci/devices/{pci_address}/driver')
    if _driver_name(pci_address) is not None:
        logging.debug(f'unbinding {pci_address} from driver')
        (driver / 'unbind').write_text(pci_address)

    driver_override = (driver / 'driver_override')
    if driver_override.exists() and driver_override.read_text() != '(null)\n':
        logging.debug(f'clearing {pci_address} driver override')
        driver_override.write_text('\n')


def ensure_driver(pci_address, driver_name, /, set_override: bool = False):
    if _driver_name(pci_address) == driver_name:
        logging.debug(f'{pci_address} already uses {driver_name}')
        return

    clear_driver(pci_address)

    if set_override:
        logging.debug(f'setting {pci_address} driver override to {driver_name}')
        Path(f'/sys/bus/pci/devices/{pci_address}/driver_override').write_text(f'{driver_name}\n')

    logging.debug(f'binding {pci_address} to {driver_name}')
    Path(f'/sys/bus/pci/drivers/{driver_name}/bind').write_text(f'{pci_address}\n')
