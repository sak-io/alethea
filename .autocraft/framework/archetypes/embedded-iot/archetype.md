# Archetype: embedded-iot

## Summary
Software running on small devices and hardware: microcontrollers, sensors, smart-home gadgets, robots, with a cloud side for data and control.

## In plain words
"I want a physical device to do something and report back." Hardware fails, networks drop, and you can't easily fix a device once it's in someone's home — so updates and recovery are designed first.

## Typical products
Sensor networks, smart-home devices, wearables, industrial monitors, hobby/maker projects.

## Questions the requirements-analyst must ask
1. What does the device sense/do, and how often? (default: ask)
2. Hardware and constraints (chip, memory, battery, connectivity)? (default: ask)
3. How does it connect (Wi-Fi, Bluetooth, cellular, LoRa)? What if offline? (default: buffer locally, sync later)
4. How are devices provisioned, identified and updated remotely? (default: signed over-the-air updates)
5. Safety impact if it malfunctions (heat, motors, medical)? (default: ask — escalate to a human expert if any)

## Architecture must cover
Hardware abstraction layer (so logic is testable off-device), firmware structure and memory/power budget, communication protocol (MQTT/HTTP/BLE) and message schema, device identity and secure provisioning, **signed OTA updates with rollback (A/B)**, watchdog and failure recovery, offline buffering, time sync, cloud ingestion (an `event-processor`/`api-service` component), device fleet management, telemetry.

## Testing strategy
- **Unit (host):** logic compiled and tested on a PC against hardware mocks.
- **Simulation / hardware-in-the-loop:** firmware against emulators or a bench device.
- **Integration:** device ↔ cloud protocol with a simulated device fleet.
- **Soak & fault tests:** power loss mid-update, network drops, corrupted messages.
- **Safety tests:** fail-safe states for anything that can cause harm.

## Walking skeleton (task 1)
Firmware builds in CI, blinks/reads one sensor, sends one authenticated message to a test broker, with host-side unit tests and a simulated device.

## CI & tooling
Cross-compile build, static analysis (clang-tidy/cppcheck/clippy), host unit tests, firmware size/memory budget check, signing step with keys in secure storage.

## Where it runs (simple defaults)
Device firmware + managed IoT broker (AWS IoT/MQTT service) + a small cloud backend.

## Security focus
Unique device credentials (no shared passwords), secure boot and signed firmware, encrypted transport, update-channel protection, physical access, debug ports disabled in production, privacy of sensor data.

## Release checklist additions
OTA update + rollback tested on real hardware; power-loss recovery verified; staged fleet rollout; memory/battery budgets met; security review of provisioning; safety sign-off by a **human expert** where relevant; rollback = A/B partition revert.

## Common pitfalls (reviewers look for these)
Logic welded to hardware (untestable); hard-coded credentials; no OTA path; blocking loops without a watchdog; memory leaks; assuming reliable networks or clocks; updates that can brick devices.

## Suggested stacks
C/C++ (PlatformIO/Zephyr) · Rust (embedded) → `rust` · MicroPython/Arduino for prototypes.
