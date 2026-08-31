#MAP-SOURCE

Static streaming data origin for the GSI Streaming Terrain v2 VRChat world.

Default GitHub Pages origin:

- `https://sakusdev.github.io/map-source/data/pc/XX_YY.jpg`
- `https://sakusdev.github.io/map-source/data/quest/XX_YY.jpg`
- `https://sakusdev.github.io/map-source/data/dem/XX_YY.gst2`

The repository stores processed, game-specific terrain assets. Elevation data is resampled/quantized into GST2 before runtime delivery.

See `DATA_ATTRIBUTION.md` before publishing generated GSI-derived data.

このレポジトリのActionsでは国土地理院の地理院タイルを一部加工しストリームしやすい形式に加工しています。出典はこちらから: https://maps.gsi.go.jp/development/ichiran.html
当リポジトリが配信しているファイルは地図としての利用はできません。
