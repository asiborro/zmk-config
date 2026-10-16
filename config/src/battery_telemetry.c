/* SPDX-License-Identifier: MIT */
/* Protocol and measurement limitations: monitoring/README.md. */
#include <errno.h>
#include <string.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <zmk/activity.h>
#include <zmk/rgb_underglow.h>
#include <zmk/workqueue.h>

#define TELEMETRY_UUID(n)                                                      \
  BT_UUID_128_ENCODE(0x50ea0000 + (n), 0x2f54, 0x4e31, 0xa2b7, 0x6cb20f70a901)
#define PACKET_SIZE 16
#define VALID BIT(0)
#define USB BIT(1)
#define USB_KNOWN BIT(2)
#define LED_ON BIT(3)
#define LED_KNOWN BIT(4)
#define CONNECTED BIT(5)
#define BATTERY_MAX_MV 4250
#define USB_MIN_MV 4400
enum power_source { POWER_UNKNOWN, POWER_BATTERY, POWER_USB };

static struct bt_uuid_128 service_uuid = BT_UUID_INIT_128(TELEMETRY_UUID(0));
static struct bt_uuid_128 local_uuid = BT_UUID_INIT_128(TELEMETRY_UUID(1));
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
static struct bt_uuid_128 peer_uuid = BT_UUID_INIT_128(TELEMETRY_UUID(2));
#endif
static const struct device *const battery =
    DEVICE_DT_GET(DT_CHOSEN(zmk_battery));
static struct k_spinlock snapshot_lock;
static struct {
  uint16_t millivolts;
  uint16_t raw_millivolts;
  uint32_t sequence;
  int64_t sampled_at;
  int64_t power_changed_at;
  bool valid;
  enum power_source power;
} local;

static uint16_t age_seconds(int64_t since) {
  return MIN((k_uptime_get() - since) / 1000, UINT16_MAX);
}

/* The MS88SF21 module ties VBUS to VDDH, so VBUSDETECT also asserts on
 * battery. Infer USB only above the Li-ion voltage range. This measures the
 * charger's system output, not the cell; a sagging USB supply can overlap
 * battery voltage. No charging/completion signal is connected to the MCU.
 */
static enum power_source supply_power(uint16_t mv) {
  return mv >= USB_MIN_MV ? POWER_USB :
         mv <= BATTERY_MAX_MV ? POWER_BATTERY : POWER_UNKNOWN;
}

/* Called only while holding snapshot_lock. */
static void update_power(enum power_source power) {
  if (power != local.power) {
    local.power = power;
    local.power_changed_at = k_uptime_get();
  }
}

static void local_packet(uint8_t packet[PACKET_SIZE]) {
  memset(packet, 0, PACKET_SIZE);
  packet[0] = 2;
  packet[1] = CONNECTED;
  packet[2] = UINT8_MAX;
  packet[3] = 0; /* Metadata age; the central adds relay delay. */
  k_spinlock_key_t key = k_spin_lock(&snapshot_lock);
  if (local.valid) {
    packet[1] |= VALID;
    if (local.power != POWER_UNKNOWN) {
      packet[1] |= USB_KNOWN | (local.power == POWER_USB ? USB : 0);
    }
  }
  sys_put_le16(local.millivolts, packet + 4);
  sys_put_le16(local.sequence ? age_seconds(local.sampled_at) : UINT16_MAX,
               packet + 6);
  sys_put_le32(local.sequence, packet + 8);
  sys_put_le16(age_seconds(local.power_changed_at), packet + 12);
  sys_put_le16(local.raw_millivolts, packet + 14);
  k_spin_unlock(&snapshot_lock, key);
#if IS_ENABLED(CONFIG_ZMK_RGB_UNDERGLOW)
  bool on;
  if (zmk_rgb_underglow_get_state(&on) == 0) {
    packet[1] |= LED_KNOWN | (on ? LED_ON : 0);
    packet[2] = zmk_rgb_underglow_calc_brt(0).b;
  }
#endif
}

static void sample_work_handler(struct k_work *work) {
  uint16_t samples[5];
  uint16_t raw = 0;
  bool valid = device_is_ready(battery);
  /* This is ZMK's battery work queue too, so ADC access cannot race its
   * battery sampler. Never perform ADC I/O from a Bluetooth callback. */
  for (int i = 0; valid && i < ARRAY_SIZE(samples); i++) {
    struct sensor_value value;
    valid = sensor_sample_fetch_chan(battery, SENSOR_CHAN_GAUGE_VOLTAGE) == 0 &&
            sensor_channel_get(battery, SENSOR_CHAN_GAUGE_VOLTAGE, &value) == 0;
    if (valid) {
      int32_t mv = value.val1 * 1000 + value.val2 / 1000;
      valid = mv >= 1500 && mv <= 6000;
      raw = mv;
      samples[i] = raw;
      if (i + 1 < ARRAY_SIZE(samples)) {
        k_sleep(K_MSEC(5));
      }
    }
  }
  if (valid) {
    for (int i = 1; i < ARRAY_SIZE(samples); i++) {
      uint16_t value = samples[i];
      int j = i;
      while (j > 0 && samples[j - 1] > value) {
        samples[j] = samples[j - 1];
        j--;
      }
      samples[j] = value;
    }
  }
  k_spinlock_key_t key = k_spin_lock(&snapshot_lock);
  update_power(valid ? supply_power(samples[2]) : POWER_UNKNOWN);
  local.valid = valid;
  if (local.valid) {
    local.millivolts = samples[2];
    local.raw_millivolts = raw;
    local.sampled_at = k_uptime_get();
    local.sequence++;
  }
  k_spin_unlock(&snapshot_lock, key);
}
K_WORK_DEFINE(sample_work, sample_work_handler);

#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
BUILD_ASSERT(CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS == 1,
             "PeaMK telemetry supports exactly one split peripheral");
static uint8_t peer_packet_cache[PACKET_SIZE];
static int64_t peer_received_at;
static bool peer_connected;
static uint32_t peer_generation;
static uint32_t request_generation;
static struct bt_conn *pending_conn;
static struct bt_gatt_read_params read_params;

static uint8_t peer_read_complete(struct bt_conn *conn, uint8_t err,
                                  struct bt_gatt_read_params *params,
                                  const void *data, uint16_t length) {
  struct bt_conn *release;
  k_spinlock_key_t key = k_spin_lock(&snapshot_lock);
  if (!err && data && length == PACKET_SIZE &&
      (((const uint8_t *)data)[0] == 1 || ((const uint8_t *)data)[0] == 2) &&
      peer_connected &&
      request_generation == peer_generation) {
    memcpy(peer_packet_cache, data, PACKET_SIZE);
    peer_received_at = k_uptime_get();
  }
  release = pending_conn;
  pending_conn = NULL;
  k_spin_unlock(&snapshot_lock, key);
  if (release) {
    bt_conn_unref(release);
  }
  return BT_GATT_ITER_STOP;
}

static void find_peer(struct bt_conn *conn, void *user_data) {
  struct bt_conn_info info;
  struct bt_conn **found = user_data;
  if (!*found && bt_conn_get_info(conn, &info) == 0 &&
      info.role == BT_CONN_ROLE_CENTRAL &&
      info.state == BT_CONN_STATE_CONNECTED &&
      bt_conn_get_security(conn) >= BT_SECURITY_L2) {
    *found = bt_conn_ref(conn);
  }
}

static void poll_peer(struct k_work *work) {
  k_spinlock_key_t key = k_spin_lock(&snapshot_lock);
  struct bt_conn *old = pending_conn ? bt_conn_ref(pending_conn) : NULL;
  k_spin_unlock(&snapshot_lock, key);
  if (old) {
    /* The callback owns the reference and params until cancellation has
     * completed; never reuse parameters on a stalled/disconnected read. */
    bt_gatt_cancel(old, &read_params);
    bt_conn_unref(old);
    return;
  }
  struct bt_conn *conn = NULL;
  bt_conn_foreach(BT_CONN_TYPE_LE, find_peer, &conn);
  if (!conn) {
    return;
  }
  read_params = (struct bt_gatt_read_params){
      .func = peer_read_complete,
      .handle_count = 0,
      .by_uuid = {.start_handle = 1,
                  .end_handle = UINT16_MAX,
                  .uuid = &local_uuid.uuid},
  };
  key = k_spin_lock(&snapshot_lock);
  pending_conn = conn;
  request_generation = peer_generation;
  k_spin_unlock(&snapshot_lock, key);
  int err = bt_gatt_read(conn, &read_params);
  if (err) {
    peer_read_complete(conn, BT_ATT_ERR_UNLIKELY, &read_params, NULL, 0);
  }
}
K_WORK_DEFINE(peer_work, poll_peer);

static void peer_connection_changed(struct bt_conn *conn, bool connected) {
  struct bt_conn_info info;
  if (bt_conn_get_info(conn, &info) != 0 || info.role != BT_CONN_ROLE_CENTRAL) {
    return;
  }
  k_spinlock_key_t key = k_spin_lock(&snapshot_lock);
  peer_connected = connected;
  peer_generation++;
  memset(peer_packet_cache, 0, sizeof(peer_packet_cache));
  peer_received_at = 0;
  k_spin_unlock(&snapshot_lock, key);
}
static void connected(struct bt_conn *conn, uint8_t err) {
  if (!err) {
    peer_connection_changed(conn, true);
  }
}
static void disconnected(struct bt_conn *conn, uint8_t reason) {
  peer_connection_changed(conn, false);
}
BT_CONN_CB_DEFINE(peamk_telemetry_connections) = {
    .connected = connected,
    .disconnected = disconnected,
};

static void remote_packet(uint8_t packet[PACKET_SIZE]) {
  k_spinlock_key_t key = k_spin_lock(&snapshot_lock);
  memcpy(packet, peer_packet_cache, PACKET_SIZE);
  if (peer_connected && (peer_packet_cache[0] == 1 || peer_packet_cache[0] == 2)) {
    uint32_t elapsed = age_seconds(peer_received_at);
    packet[3] = MIN(UINT8_MAX, packet[3] + elapsed);
    sys_put_le16(MIN(UINT16_MAX, sys_get_le16(packet + 6) + elapsed),
                 packet + 6);
    sys_put_le16(MIN(UINT16_MAX, sys_get_le16(packet + 12) + elapsed),
                 packet + 12);
  } else {
    packet[0] = 2;
    packet[1] = peer_connected ? CONNECTED : 0;
    packet[2] = UINT8_MAX;
    sys_put_le16(UINT16_MAX, packet + 6);
  }
  k_spin_unlock(&snapshot_lock, key);
}
#endif

static ssize_t read_local(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                          void *buf, uint16_t len, uint16_t offset) {
  uint8_t packet[PACKET_SIZE];
  local_packet(packet);
  return bt_gatt_attr_read(conn, attr, buf, len, offset, packet,
                           sizeof(packet));
}
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
static ssize_t read_peer(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                         void *buf, uint16_t len, uint16_t offset) {
  uint8_t packet[PACKET_SIZE];
  remote_packet(packet);
  return bt_gatt_attr_read(conn, attr, buf, len, offset, packet,
                           sizeof(packet));
}
#endif

/* Zephyr sorts static services by name. Append after the existing HID, BAS,
 * DIS and split services so their attribute handles remain unchanged. */
BT_GATT_SERVICE_DEFINE(
    zz_peamk_telemetry, BT_GATT_PRIMARY_SERVICE(&service_uuid),
    BT_GATT_CHARACTERISTIC(&local_uuid.uuid, BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ_ENCRYPT, read_local, NULL, NULL),
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
    BT_GATT_CHARACTERISTIC(&peer_uuid.uuid, BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ_ENCRYPT, read_peer, NULL, NULL),
#endif
);

static void tick(struct k_timer *timer) {
  k_work_submit_to_queue(zmk_workqueue_lowprio_work_q(), &sample_work);
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
  k_work_submit_to_queue(zmk_workqueue_lowprio_work_q(), &peer_work);
#endif
}
K_TIMER_DEFINE(telemetry_timer, tick, NULL);

static int telemetry_init(void) {
  local.power_changed_at = k_uptime_get();
  /* Continue sampling in idle; the timer naturally stops in deep power-off.
   * Reading telemetry never marks the keyboard active or prevents sleep. */
  k_timer_start(&telemetry_timer, K_SECONDS(3), K_SECONDS(5));
  return 0;
}
SYS_INIT(telemetry_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
