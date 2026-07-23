# D2-01-12 Channel-Specific Configuration Entities

## Overview
All configuration entities for D2-01-12 are now properly split per channel (ch0 and ch1) with command templates that target the correct channel using the appropriate EnOcean commands.

## Command 2: Actuator Set Local (Per-Channel Configuration)

### Dim Timers (DT1, DT2, DT3)
Configure dimming speeds when transitioning between brightness levels.

- **Entity Type**: `number`
- **Entities**: 
  - `DT1_ch0`, `DT2_ch0`, `DT3_ch0` (Channel 0)
  - `DT1_ch1`, `DT2_ch1`, `DT3_ch1` (Channel 1)
- **User Input**: Seconds (0-127.5s, step 0.5)
- **Conversion**: Multiplied by 2 for device (device uses 0.5s units)
- **Command**: `{"CMD": 2, "IO": <channel>, "DT1|DT2|DT3": <value>}`
- **Example**: Setting DT1_ch0 to 5.0s sends `{"CMD": 2, "IO": 0, "DT1": 10}`

### Default State (DS)
Configure output state after power restore/failure.

- **Entity Type**: `select`
- **Entities**: `default_state_ch0`, `default_state_ch1`
- **Options**:
  - "OFF on power restore" (value: 0)
  - "ON on power restore" (value: 1)
  - "Remember last state" (value: 2)
- **Command**: `{"CMD": 2, "IO": <channel>, "DS": <value>}`

### Local Control (LC)
Enable/disable physical button control at the device.

- **Entity Type**: `switch`
- **Entities**: `local_control_ch0`, `local_control_ch1`
- **Command ON**: `{"CMD": 2, "IO": <channel>, "LC": 1}`
- **Command OFF**: `{"CMD": 2, "IO": <channel>, "LC": 0}`

## Command 11: External Interface Settings (Per-Channel Configuration)

### External Button Mode (EBM)
Configure how the device interprets external button/switch connections.

- **Entity Type**: `select`
- **Entities**: `external_button_mode_ch0`, `external_button_mode_ch1`
- **Options**:
  - "Not applicable" (value: 0)
  - "External Switch" (value: 1)
  - "External Push Button" (value: 2)
  - "Auto detect" (value: 3)
- **Command**: `{"CMD": 11, "IO": <channel>, "EBM": <value>}`

### Switch Type (SWT)
Configure 2-state switch behavior.

- **Entity Type**: `select`
- **Entities**: `switch_type_ch0`, `switch_type_ch1`
- **Options**:
  - "Toggle (each change toggles ON/OFF)" (value: 0)
  - "2-state (ON when closed, OFF when open)" (value: 1)
- **Command**: `{"CMD": 11, "IO": <channel>, "SWT": <value>}`

### Auto Off Timer (AOT)
Automatically turn off after specified time.

- **Entity Type**: `select` (presets) + `number` (custom)
- **Entities**: 
  - `auto_off_preset_ch0`, `auto_off_custom_ch0` (Channel 0)
  - `auto_off_preset_ch1`, `auto_off_custom_ch1` (Channel 1)
- **Presets**: Disabled, 5min, 15min, 30min, 1h, 2h, 6h, 12h, 24h, Keep device setting
- **Custom Range**: 1-65534 seconds
- **Command**: `{"CMD": 11, "IO": <channel>, "AOT": <value>}`

### Delay OFF Timer (DOT)
Delay turning off by specified time.

- **Entity Type**: `select` (presets) + `number` (custom)
- **Entities**: 
  - `delay_off_preset_ch0`, `delay_off_custom_ch0` (Channel 0)
  - `delay_off_preset_ch1`, `delay_off_custom_ch1` (Channel 1)
- **Presets**: Disabled, 5min, 15min, 30min, 1h, 2h, 6h, 12h, 24h, Keep device setting
- **Custom Range**: 1-65534 seconds
- **Command**: `{"CMD": 11, "IO": <channel>, "DOT": <value>}`

## Device-Level Entities (Not Per-Channel)

### Local Control Status (LC)
Binary sensor showing whether local control is currently enabled (read from Command 4 status responses).

- **Entity Type**: `binary_sensor` (auto-generated from EEP.xml)
- **Entity**: `LC`
- **Values**: on=enabled, off=disabled
- **Note**: Read-only status sensor. Use `local_control_ch0` and `local_control_ch1` switches to configure.

### Measurement Unit (UN)
Configure energy/power measurement reporting (Command 5).

- **Entity Type**: `select` (auto-generated from EEP.xml)
- **Entity**: `UN`
- **Values**: Energy [Ws/Wh/KWh], Power [W/KW]
- **Note**: Device-level configuration, not per-channel

### Error Level (EL)
Hardware status indicator from status response (Command 4).

- **Entity Type**: `sensor` (auto-generated from EEP.xml)
- **Entity**: `EL`
- **Values**: 0=OK, 1=Warning, 2=Failure, 3=Not supported
- **Note**: Read-only diagnostic, reported in status responses

## Auto-Generation Prevention

To avoid duplicate entities, the following fields are explicitly prevented from auto-generation since they have per-channel implementations:

- **DT1, DT2, DT3**: Skipped (use `DT1_ch0`, `DT1_ch1`, etc.)
- **DS**: Skipped (use `default_state_ch0`, `default_state_ch1`)
- **EBM**: Skipped (use `external_button_mode_ch0`, `external_button_mode_ch1`)
- **SWT**: Skipped (use `switch_type_ch0`, `switch_type_ch1`)
- **AOT, DOT**: Overridden as hidden config entities (use preset/custom entities per channel)

## Summary of Changes

### Before
- DT1/DT2/DT3: Only configured for channel 0 (`"IO": 0`)
- DS: No command template (couldn't configure)
- EBM: No command template (couldn't configure)
- LC: Only binary_sensor for status, no config entity
- SWT: Not in YAML at all

### After
- ✅ All configuration entities split per channel (ch0/ch1)
- ✅ Each entity has proper command template with correct channel
- ✅ Local Control now has config switches in addition to status binary_sensor
- ✅ Switch Type (SWT) configuration added
- ✅ All timer values in user-friendly seconds units
- ✅ All config entities marked with `entity_category: "config"`

## Usage Example

To configure channel 0 for dimming with 2-second fade:
1. Set `DT1_ch0` to `2.0` seconds
2. When turning on/off lights, use dim command: `{"CMD": 1, "DV": 1, "IO": 0, "OV": 100}`
   - `DV: 1` means "use dim timer 1"

To set channel 1 to remember its last state after power failure:
1. Select "Remember last state" in `default_state_ch1`
2. Device stores this setting and applies on next power restore
