/* XIAO nRF52840 LoRa bootloader-update layout.
 *
 * The application ends at 0xE0000. OTAFIX owns 0xE0000..0xEA000 as a 40 KiB internal scratch bank
 * while replacing its own padded 0xF4000..0xFE000 bootloader region from the verified QSPI package.
 */

SEARCH_DIR(.)
GROUP(-lgcc -lc -lnosys)

MEMORY
{
  FLASH (rx)     : ORIGIN = 0x27000, LENGTH = 0xE0000 - 0x27000

  /* To keep data in RAM across resets */
  PERSISTENT_RAM (rwx) : ORIGIN = 0x20006000, LENGTH = 8

  /* Separate reset-retained state; clock and OTA handoff ABIs stay fixed. */
  REGULATOR_RECOVERY_RAM (rwx) : ORIGIN = 0x20006050, LENGTH = 64

  /* SRAM required by SoftDevice depend on
   * - Attribute Table Size (Number of Services and Characteristics)
   * - Vendor UUID count
   * - Max ATT MTU
   * - Concurrent connection peripheral + central + secure links
   * - Event Len, HVN queue, Write CMD queue
   */
  RAM (rwx) :  ORIGIN = 0x20006090, LENGTH = 0x20040000 - 0x20006090
}

SECTIONS
{
  . = ALIGN(4);
  .persistent (NOLOAD) :
  {
    KEEP(*(.persistent_magic))
    KEEP(*(.persistent_data))
    . = ALIGN(4);
  } > PERSISTENT_RAM

  .regulator_recovery (NOLOAD) :
  {
    __regulator_recovery_start__ = .;
    KEEP(*(.regulator_recovery))
    . = ORIGIN(REGULATOR_RECOVERY_RAM) + LENGTH(REGULATOR_RECOVERY_RAM);
    __regulator_recovery_end__ = .;
  } > REGULATOR_RECOVERY_RAM

  ASSERT(__regulator_recovery_start__ == 0x20006050,
         "regulator recovery start must remain fixed")
  ASSERT(__regulator_recovery_end__ == 0x20006090,
         "regulator recovery reservation must remain exactly 64 bytes")
  ASSERT(ORIGIN(RAM) == __regulator_recovery_end__,
         "application RAM must start after regulator recovery")

  . = ALIGN(4);
  .svc_data :
  {
    PROVIDE(__start_svc_data = .);
    KEEP(*(.svc_data))
    PROVIDE(__stop_svc_data = .);
  } > RAM

  .fs_data :
  {
    PROVIDE(__start_fs_data = .);
    KEEP(*(.fs_data))
    PROVIDE(__stop_fs_data = .);
  } > RAM
}

INCLUDE "nrf52_common.ld"
