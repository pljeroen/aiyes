# Android Emulator Basic Flow

Purpose: inspect and control a trusted adb-connected emulator or device.

Prerequisites:

- Android SDK platform-tools installed.
- An emulator or trusted device is visible in `adb devices`.
- The app exposes useful accessibility metadata: text, content descriptions, resource IDs, or Compose semantics/test tags.

```bash
adb devices
aieyes doctor
aieyes session start --backend android --device-serial emulator-5554 -- \
    adb -s emulator-5554 shell monkey -p com.example.app 1
aieyes inspect --no-screenshot --tree-depth 4
aieyes find button "Continue"
aieyes action <node-id> click
aieyes wait text "Home" --timeout 10
aieyes screenshot
aieyes inspect --no-screenshot --tree-depth 4
aieyes session stop
```

Verify:

- `doctor` reports adb and an attached device.
- `inspect` returns an Android UIAutomator tree.
- `wait` observes the expected post-click state.

Android limitations:

- No resize support.
- Fewer states than Linux AT-SPI.
- `wait-stable` and `diff` have restricted accuracy.
- Coordinate fallback is less reliable than stable accessibility metadata.
