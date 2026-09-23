#!/usr/bin/env python3
"""Read the bonded BLE UART CCCD across two T1000-E connections.

This is local-only: it subscribes to notifications once, disconnects, then
reconnects without re-subscribing and reads the descriptor. It never changes
radio settings, sends LoRa traffic, or erases storage.
"""

import asyncio
import argparse
import json
import os
import sys

from bleak import BleakClient, BleakScanner


ADDRESS = None
PIN = "123456"
UART_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


async def pair_with_pin():
    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.enumeration import (
        DevicePairingKinds,
        DevicePairingProtectionLevel,
    )

    address = int(ADDRESS.replace(":", ""), 16)
    device = await BluetoothLEDevice.from_bluetooth_address_async(address)
    if device is None:
        raise RuntimeError("Windows cannot open the advertising BLE device")
    pairing = device.device_information.pairing
    if pairing.is_paired:
        return "already paired"
    custom = pairing.custom

    def on_request(_sender, args):
        if args.pairing_kind == DevicePairingKinds.PROVIDE_PIN:
            args.accept(PIN)
        elif args.pairing_kind == DevicePairingKinds.CONFIRM_ONLY:
            args.accept()

    token = custom.add_pairing_requested(on_request)
    try:
        result = await custom.pair_async(
            DevicePairingKinds.PROVIDE_PIN | DevicePairingKinds.CONFIRM_ONLY,
            DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION,
        )
        return str(result.status)
    finally:
        custom.remove_pairing_requested(token)
        device.close()


async def descriptor_state(subscribe):
    async with BleakClient(ADDRESS, timeout=20) as client:
        tx = client.services.get_characteristic(UART_TX)
        if tx is None:
            raise RuntimeError("Nordic UART TX characteristic unavailable")
        cccd = next(
            (item for item in tx.descriptors if item.uuid.lower().startswith("00002902")),
            None,
        )
        if cccd is None:
            raise RuntimeError("Nordic UART CCCD unavailable")
        if subscribe:
            await client.start_notify(tx, lambda *_: None)
            await asyncio.sleep(0.4)
        value = await client.read_gatt_descriptor(cccd.handle)
        return {"cccd": bytes(value).hex(), "handle": cccd.handle}


async def subscribe_then_abort():
    client = BleakClient(ADDRESS, timeout=20)
    await client.connect()
    tx = client.services.get_characteristic(UART_TX)
    await client.start_notify(tx, lambda *_: None)
    await asyncio.sleep(0.4)
    cccd = next(item for item in tx.descriptors
                if item.uuid.lower().startswith("00002902"))
    value = await client.read_gatt_descriptor(cccd.handle)
    print(json.dumps({"subscribed_before_abrupt_exit": bytes(value).hex()}), flush=True)


async def main(pair, mode):
    discovered = await BleakScanner.discover(timeout=10)
    if not any(item.address.upper() == ADDRESS for item in discovered):
        raise RuntimeError("T1000-E is not advertising; stop the phone connection first")
    if pair:
        print("pair:", await pair_with_pin())
        await asyncio.sleep(1)
    if mode == "abort":
        await subscribe_then_abort()
        os._exit(0)
    elif mode == "subscribe":
        print(json.dumps({"subscribed": await descriptor_state(True)}))
    elif mode == "inspect":
        print(json.dumps({"without_subscribe": await descriptor_state(False)}))
    else:
        first = await descriptor_state(True)
        await asyncio.sleep(1)
        second = await descriptor_state(False)
        print(json.dumps({"first": first, "reconnect_without_subscribe": second}))
        if first["cccd"] != "0100" or second["cccd"] != "0100":
            raise RuntimeError("UART notifications did not survive reconnect")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True,
                        help="Bluetooth address of the test T1000-E")
    parser.add_argument("--pin", default="123456",
                        help="pairing PIN, used only with --pair")
    parser.add_argument("--pair", action="store_true")
    parser.add_argument("--mode", choices=("roundtrip", "subscribe", "inspect", "abort"),
                        default="roundtrip")
    args = parser.parse_args()
    ADDRESS = args.address.upper()
    PIN = args.pin
    try:
        asyncio.run(main(args.pair, args.mode))
    except Exception as exc:
        print(f"BLE CCCD test failed: {exc}", file=sys.stderr)
        raise
