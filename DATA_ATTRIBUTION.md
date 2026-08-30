# Data attribution

This project uses processed geospatial data from several upstream sources. Generated runtime files are game-specific derivatives for VRChat streaming and are not represented as authoritative surveying, navigation, or cadastral products.

## Elevation

地形標高データ：地理院タイル（標高タイル（基盤地図情報数値標高モデル））を加工して作成。

Source: Geospatial Information Authority of Japan (国土地理院).

The runtime `.gst2` files are resampled and quantized derivatives for VRChat terrain streaming.

## Imagery

When imagery generated from GSI Tiles is included, retain the attribution required for the exact imagery layer used. For 全国最新写真（シームレス） and other composite imagery, check the GSI layer metadata for any additional source-specific credit before publishing the dataset.

## Buildings

Building footprints and attributes used to generate `Server/data/buildings/**/*.gsb1` are derived from the Overture Maps Foundation Buildings theme.

The Overture Buildings theme is distributed under the Open Database License (ODbL) 1.0. The generated `.gsb1` building tile database is intended to be redistributed under ODbL 1.0 as a derivative database.

Required/source attribution for the Buildings theme includes, as applicable to the Overture release used by the generator:

- © OpenStreetMap contributors — Open Database License.
- Esri Community Maps contributors — CC BY 4.0.
- Microsoft Global ML Building Footprints — Open Database License.
- Google Open Buildings — CC BY 4.0.
- Other compatible sources listed by Overture Maps Foundation for the selected release.

Overture attribution and licensing reference: https://docs.overturemaps.org/attribution/
Overture Buildings documentation: https://docs.overturemaps.org/guides/buildings/

For an in-world or application attribution surface, a concise credit such as `Buildings: Overture Maps Foundation / © OpenStreetMap contributors — ODbL 1.0` should link or otherwise provide access to the full attribution above.
