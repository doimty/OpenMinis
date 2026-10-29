# iOS 15 TrollStore IPA

Unsigned/ad-hoc sideload package for TrollStore or a jailbroken device. It is
not App Store signed. Team `29S5S789Z7` is not used.

## What CI produces

After a green `Probe iOS 15 app target` step, `scripts/package_ios15_ipa.py`
copies `Debug-iphoneos/Minis.app`, drops appex newer than iOS 15.0, ad-hoc
signs on Darwin with `scripts/ios15-trollstore.entitlements`, and uploads:

- `minis-ios15-trollstore-ipa` artifact: `Minis-*-ios15-trollstore.ipa` plus `manifest.json`

Stripped from this IPA because they cannot load on iOS 15.0:

- `MinisFileProvider.appex` (16.0)
- `AgentWidgetExtension.appex` (16.2)

`MinisShare.appex` stays if its MinimumOSVersion is 15.0.

## Install

1. Download the IPA.
2. Share it to TrollStore and install.
3. If TrollStore asks to persist, confirm.

This is a Debug build. iCloud, HealthKit, HomeKit, NFC, WeatherKit, widgets,
and the File Provider will not work. App Group `group.com.openminis.app` is
requested; TrollStore may still drop it.

A green compile/IPA job is not M3 device acceptance.
