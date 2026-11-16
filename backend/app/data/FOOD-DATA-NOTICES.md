# Food catalogue data notices — v1 (migration 0031)

This file accompanies food_catalog_v1.json. Per-entry provider, source identifier,
version, edible state and modifications are preserved in source_info and returned
by the food APIs. No source grants endorsement of FitnessAgent.

## USDA

USDA Agricultural Research Service, FoodData Central, SR Legacy (2018).
Data are CC0. Source IDs and original field evidence are recorded in
docs/food-source-audit/source-matrix-v3.json.

- https://fdc.nal.usda.gov/download-datasets/
- https://fdc.nal.usda.gov/api-guide/
- https://creativecommons.org/publicdomain/zero/1.0/

## Taiwan Food and Drug Administration

TFDA 食品營養成分資料集, Government Data Open License v1.
Selected per100g fields and Chinese display names; sample identifiers and
retrieval evidence retained. These samples are not national market averages.

- https://data.gov.tw/dataset/8543
- https://data.gov.tw/license

## MEXT

文部科学省 日本食品標準成分表（八訂）増補2023年／食品成分データベース.
Source-attributed reuse; selected nutrient fields and translated display names.
Original food IDs, cooking state, calculation/estimation notes retained.

- https://fooddb.mext.go.jp/
- https://www.mext.go.jp/b_menu/1351168.htm

## AFCD — N15 data only

© Food Standards Australia New Zealand (FSANZ).
Australian Food Composition Database, Release 3, December 2025, F009805.
The adapted AFCD entry, including its translated description, is distributed
under the AFCD Data User Licence Agreement (based on CC BY-SA 3.0 Australia),
not the generic FSANZ website licence. This notice does not relicense the
independent application code or other independent sources in this collection.

- Source: https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd/search/food/F009805
- Applicable licence: https://www.foodstandards.gov.au/science-data/monitoringnutrients/afcd/datauserlicenceagreement
- Underlying licence: https://creativecommons.org/licenses/by-sa/3.0/au/

Changes: selected per100g fields; name and descriptive notes translated into
Chinese; energy converted from 383 kJ using 4.184 kJ/kcal. No FSANZ endorsement.
The Work is based on Australian data and Australian data may not be appropriate
for use in other countries.

There are limitations associated with food composition databases. Food composition
data used in the database or databases may represent an average of the nutrient
content of a particular sample of foods and ingredients, determined at a particular
time. The nutrient composition of foods and ingredients can vary substantially
between batches and brands because of a number of factors, including changes in
season, processing practices and ingredient source, and methods of calculation.

The source's warranty disclaimer and liability limitations remain applicable;
see sections 7 and 8 of the linked AFCD Data User Licence Agreement. The data are
provided as-is; no accuracy or fitness-for-purpose warranty is introduced here.
Preserve this notice and the per-entry source_info when redistributing the data.
