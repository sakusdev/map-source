# Data attribution

This project uses processed geospatial data from several upstream sources. Generated runtime files are game-specific derivatives for VRChat streaming and are not represented as authoritative surveying, navigation, or cadastral products.

## Elevation

地形標高データ：地理院タイル（標高タイル（基盤地図情報数値標高モデル））を加工して作成。

Source: Geospatial Information Authority of Japan (国土地理院). link: https://maps.gsi.go.jp/development/ichiran.html

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

## Vegetation / forest placement

Forest footprints used to generate `Server/data/regions/*/vegetation/**/*.gsv1` are derived from the Overture Maps Foundation Base theme, `land_cover` feature type, filtered to `subtype=forest`. Overture land-cover features are sourced from ESA WorldCover. The Overture Base theme is distributed under ODbL 1.0; retain the Overture attribution appropriate to the release used by the generator.

The generated GSV1 files contain deterministic game-oriented placement samples inside those GIS forest footprints. They do not claim individual-tree positions or tree-species accuracy.

Overture Base / land-cover documentation: https://docs.overturemaps.org/guides/base/
Overture attribution and licensing reference: https://docs.overturemaps.org/attribution/

Tree meshes used for rendering are from Quaternius, `Textured LowPoly Trees`, dedicated under CC0 1.0 Universal. The geometry assets are independent of the Overture-derived placement database.

## Water

Water surfaces used to generate `Server/data/regions/*/water/**/*.gsw1` are derived from the Overture Maps Foundation Base theme, `water` feature type. Overture water features represent ocean and inland water bodies and are sourced from OpenStreetMap. The generator uses polygon and multi-polygon water geometry directly and converts river/stream/canal line geometry into narrow game-oriented water strips before clipping and triangulating each 4 km runtime tile.

The generated GSW1 files are simplified, triangulated rendering derivatives rather than authoritative hydrographic or navigation data. Inland surface elevation is approximated from the project DEM and ocean surfaces use sea level with a small rendering offset.

Source credit: Overture Maps Foundation / © OpenStreetMap contributors — Open Database License (ODbL) 1.0.
Overture Water schema: https://docs.overturemaps.org/schema/reference/base/water/
Overture attribution and licensing reference: https://docs.overturemaps.org/attribution/
