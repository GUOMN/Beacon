# ESP32-C3 WS2812 status light

ESP-IDF project for an ESP32-C3 Mini driving a 36-pixel WS2812 LED strip. Each
pixel independently and smoothly fades between random colors while a breathing
wave flows along the strip.

## Wiring

| ESP32-C3 Mini | WS2812 strip |
| --- | --- |
| GPIO 8 (default) | DIN |
| GND | GND |
| External 5 V | 5 V |

Use a suitable external 5 V supply for more than a few LEDs and connect its
ground to the ESP32 ground. A 330-470 ohm resistor in series with DIN and a
large capacitor (for example, 1000 uF) across the strip supply are recommended.

## Build and flash

Install ESP-IDF 5.1 or newer and open its configured terminal, then run:

```powershell
idf.py set-target esp32c3
idf.py menuconfig
idf.py build
idf.py -p COM3 flash monitor
```

Replace `COM3` with the board's serial port. The three commonly adjusted values
(`LED_COUNT`, `LED_BRIGHTNESS`, and `BREATH_SPEED`) are grouped at the top of
`main/main.c`. GPIO and frame interval remain under **WS2812 流水灯配置** in
`menuconfig`.

The first build downloads the official `espressif/led_strip` component through
the ESP-IDF Component Manager.
