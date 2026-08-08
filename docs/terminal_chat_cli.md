# Terminal Chat CLI

Below are the commands you can enter into the Terminal Chat clients:

## Companion USB mode

A Companion USB build starts in the normal binary Companion protocol at
115200 baud. To use this terminal from the same firmware image, connect a
serial terminal and send this exact sequence:

```
+++MESHCORE-TERM-START
```

Send the following exact sequence to return to the binary protocol:

```
+++MESHCORE-TERM-STOP
```

Closing the serial connection also returns native-USB devices to binary mode.
Boards whose USB connector is implemented by a USB-to-UART bridge cannot
observe the host closing the port; on those boards, use the stop sequence or
reboot the device.

Both modes use the same port at 115200. Selecting 57600 is not a portable mode
switch: native USB CDC devices ignore the requested baud, while USB-to-UART
devices really change the UART timing and receive corrupt data. Binary mode is
the framed Companion API used by apps and `meshcli`; close the terminal before
opening that port from an app.

For example:

```sh
picocom --baud 115200 /dev/ttyACM0
```

## Commands

```
set freq {frequency}
```
Set the LoRa frequency. Example:  set freq 915.8

```
set tx {tx-power-dbm}
```
Sets LoRa transmit power in dBm.

```
set name {name}
```
Sets your advertisement name.

```
set lat {latitude}
```
Sets your advertisement map latitude. (decimal degrees)

```
set lon {longitude}
```
Sets your advertisement map longitude. (decimal degrees)

```
set dutycycle {percent}
```
Sets the transmit duty cycle limit (1-100%). Example: `set dutycycle 10` for 10%.

```
set af {air-time-factor}
```
Sets the transmit air-time-factor. Deprecated - use `set dutycycle` instead.


```
time {epoch-secs}
```
Set the device clock using UNIX epoch seconds. Example:  time 1738242833


```
advert
```
Sends an advertisement packet

```
clock
```
Displays current time per device's clock.


```
ver
```
Shows the device version and firmware build date.

```
card
```
Displays *your* 'business card', for others to manually _import_

```
import {card}
```
Imports the given card to your contacts.

```
list {n}
```
List all contacts by most recent. (optional {n}, is the last n by advertisement date)

```
to
```
Shows the name of current recipient contact. (for subsequent 'send' commands)

```
to {name-prefix}
```
Sets the recipient to the _first_ matching contact (in 'list') by the name prefix. (ie. you don't have to type whole name)

```
send {text}
```
Sends the text message (as DM) to current recipient.

```
reset path
```
Resets the path to current recipient, for new path discovery.

```
public {text}
```
Sends the text message to the built-in 'public' group channel
