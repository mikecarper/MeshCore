/**************************************************************************/
/*!
    @file     bluefruit.cpp
    @author   hathach (tinyusb.org)

    @section LICENSE

    Software License Agreement (BSD License)

    Copyright (c) 2018, Adafruit Industries (adafruit.com)
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:
    1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
    2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.
    3. Neither the name of the copyright holders nor the
    names of its contributors may be used to endorse or promote products
    derived from this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS ''AS IS'' AND ANY
    EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
    WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
    DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER BE LIABLE FOR ANY
    DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
    (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
    LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
    ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
    SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
/**************************************************************************/

#include "bluefruit.h"

void AdafruitBluefruit::_ble_handler(ble_evt_t* evt)
{
  // conn handle has fixed offset for all events
  uint16_t const conn_hdl = evt->evt.common_evt.conn_handle;
  BLEConnection* conn = this->Connection(conn_hdl);

  LOG_LV2("BLE", "%s : Conn Handle = %d", dbg_ble_event_str(evt->header.evt_id), conn_hdl);

  // GAP handler
  if ( conn )
  {
    conn->_eventHandler(evt);
    Security._eventHandler(evt);
  }

  switch(evt->header.evt_id)
  {
    case BLE_GAP_EVT_CONNECTED:
    {
      // Turn on Conn LED
      _stopConnLed();
      _setConnLed(true);

      ble_gap_evt_connected_t const * para = &evt->evt.gap_evt.params.connected;
      ble_gap_addr_t const* peer_addr = &para->peer_addr;

      (void) peer_addr;

      LOG_LV2("GAP", "MAC = %02X:%02X:%02X:%02X:%02X:%02X, Type = %d, Resolved = %d",
              peer_addr->addr[5], peer_addr->addr[4], peer_addr->addr[3], peer_addr->addr[2], peer_addr->addr[1], peer_addr->addr[0],
              peer_addr->addr_type, peer_addr->addr_id_peer);
      LOG_LV2("GAP", "Conn Interval = %.2f ms, Latency = %d, Supervisor Timeout = %d ms",
              para->conn_params.max_conn_interval*1.25f, para->conn_params.slave_latency, 10*para->conn_params.conn_sup_timeout);

      if ( _connection[conn_hdl] )
      {
        LOG_LV1("GAP", "Connection is already in used, something wrong !!");
        delete _connection[conn_hdl];
        _connection[conn_hdl] = NULL;
      }

      // Transmission buffer pool
      uint8_t const hvn_qsize = (para->role == BLE_GAP_ROLE_PERIPH) ? _sd_cfg.prph.hvn_qsize : _sd_cfg.central.hvn_qsize;
      uint8_t const wrcmd_qsize = (para->role == BLE_GAP_ROLE_PERIPH) ? _sd_cfg.prph.wrcmd_qsize : _sd_cfg.central.wrcmd_qsize;

      _connection[conn_hdl] = new BLEConnection(conn_hdl, para, hvn_qsize, wrcmd_qsize);
      conn = _connection[conn_hdl];

      // Invoke connect callback
      if ( conn->getRole() == BLE_GAP_ROLE_PERIPH )
      {
        if ( Periph._connect_cb ) ada_callback(NULL, 0, Periph._connect_cb, conn_hdl);
      }else
      {
        if ( Central._connect_cb ) ada_callback(NULL, 0, Central._connect_cb, conn_hdl);
      }
    }
    break;

    case BLE_GAP_EVT_DISCONNECTED:
    {
      ble_gap_evt_disconnected_t const* para = &evt->evt.gap_evt.params.disconnected;

      LOG_LV2("GAP", "Disconnect Reason: %s", dbg_hci_str(evt->evt.gap_evt.params.disconnected.reason));

      // Turn off Conn LED If not connected at all
      if ( !this->connected() ) _setConnLed(false);

      // Invoke disconnect callback
      if ( conn->getRole() == BLE_GAP_ROLE_PERIPH )
      {
        if ( Periph._disconnect_cb ) ada_callback(NULL, 0, Periph._disconnect_cb, conn_hdl, para->reason);
      }else
      {
        if ( Central._disconnect_cb ) ada_callback(NULL, 0, Central._disconnect_cb, conn_hdl, para->reason);
      }

      delete _connection[conn_hdl];
      _connection[conn_hdl] = NULL;
    }
    break;

    case BLE_GAP_EVT_RSSI_CHANGED:
    {
      ble_gap_evt_rssi_changed_t const * rssi_changed = &evt->evt.gap_evt.params.rssi_changed;
      if ( _rssi_cb )
      {
         ada_callback(NULL, 0, _rssi_cb, conn_hdl, rssi_changed->rssi);
      }
    }
    break;

    case BLE_EVT_USER_MEM_REQUEST:
      // We will handle Long Write sequence (RW Authorize PREP_WRITE_REQ)
      sd_ble_user_mem_reply(conn_hdl, NULL);
    break;

    case BLE_EVT_USER_MEM_RELEASE:
      // nothing to do
    break;

    default: break;
  }

  Advertising._eventHandler(evt);
  Scanner._eventHandler(evt);

  /*------------- BLE Peripheral Events -------------*/
  /* Only handle Peripheral events with matched connection handle
   * or a few special one
   * - Connected event
   * - Advertising timeout (could be connected and advertising at the same time)
   */
  if ( conn_hdl       == _conn_hdl             ||
       evt->header.evt_id == BLE_GAP_EVT_CONNECTED)
  {
    switch ( evt->header.evt_id  )
    {
      case BLE_GAP_EVT_CONNECTED:
      {
        ble_gap_evt_connected_t* para = &evt->evt.gap_evt.params.connected;

        if (para->role == BLE_GAP_ROLE_PERIPH)
        {
          _conn_hdl = evt->evt.gap_evt.conn_handle;
        }
      }
      break;

      case BLE_GAP_EVT_DISCONNECTED:
        _conn_hdl = BLE_CONN_HANDLE_INVALID;
      break;

      default: break;
    }
  }

  // Periph event handler (also handle generic non-connection event)
  if ( (conn == NULL) || (conn->getRole() == BLE_GAP_ROLE_PERIPH) )
  {
    Periph._eventHandler(evt);
  }

  // Central Event Handler (also handle generic non-connection event)
  if ( (conn == NULL) || (conn->getRole() == BLE_GAP_ROLE_CENTRAL) )
  {
    Central._eventHandler(evt);
  }

  // Discovery Event Handler
  if ( Discovery.begun() ) Discovery._eventHandler(evt);

  // GATTs characteristics event handler
  Gatt._eventHandler(evt);

  // User callback if set
  if (_event_cb) _event_cb(evt);
}

