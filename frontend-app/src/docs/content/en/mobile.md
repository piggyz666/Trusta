# Phone access

The web app and the phone app are the same pages, adapting to the screen. By default TRUSTA only opens on the computer it runs on; to use a phone, switch access to local network or tunnel and pair once.

## 1. Turn on local-network access

```bash
python -m scripts.setup
```

Choose **local network** at the access step, then restart TRUSTA.

## 2. Pair your phone

Either way works:

- **In the web app**: open 设置 → 访问与设备 (Settings → Access & devices), generate a pairing code and scan it with your phone.
- **In a terminal**:

```bash
python -m scripts.pair
```

It shows the pairing address, a QR code (when the `qrcode` package is installed) and a 6-digit code. Put the phone on the same Wi-Fi or hotspot, then scan or open the address in the phone's browser. The code is valid for 5 minutes and works once.

Once paired, the phone keeps its own device token and opens directly from then on. Use the browser's “Add to Home Screen” to run it full-screen like an app.

## Managing devices

```bash
python -m scripts.pair --devices        # list paired devices
python -m scripts.pair --revoke DEVICE  # revoke one
```

You can also view and revoke devices under 设置 → 访问与设备 (Settings → Access & devices).

## Away from your network

If you already have a public address such as a Cloudflare Tunnel, choose **tunnel** in the wizard and enter the hostname (without `https://`). Without a tunnel hostname it falls back to local network.

> On local network and tunnel, every device must pair before it can use TRUSTA — but “this computer only” remains the safest setting.
