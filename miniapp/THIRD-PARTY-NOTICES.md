# Motion Primitives — Text Shimmer Wave

The loading feedback adapts the accepted Text Shimmer Wave reference into CSS-only
Taro text, with FitnessAgent colors and loading/page lifecycle control. No Motion
runtime dependency is added.

Source: https://github.com/ibelick/motion-primitives/blob/main/components/core/text-shimmer-wave.tsx

License: https://github.com/ibelick/motion-primitives/blob/main/LICENCE.md

MIT License

Copyright (c) 2024 ibelick

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Radix Icons — Cross2Icon

The close icon in `src/assets/icons/close-radix.svg` is rendered from
`@radix-ui/react-icons` 1.3.2 Cross2Icon, with the product's foreground color.
Source: https://github.com/radix-ui/icons

MIT License

Copyright (c) 2022 WorkOS

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
# Capsule navigation icons

The navigation icons are generated from the Lucide React library used in the
accepted isolated prototype: Dumbbell, Utensils, MessageCircle, UserRound.
Source: https://lucide.dev/ . ISC license and copyright notice are preserved at
`src/assets/navigation/LICENSE.txt`. Both color variants use the same original
library geometry. No new runtime icon dependency is added to the miniapp.

# Food nutrition data

Food data are delivered by the backend; individual source identifiers, licences
and limitations are available under “查看依据”. USDA FoodData Central (CC0),
TFDA (Government Data Open License v1), MEXT (attributed reuse) and FSANZ AFCD
are independent data sources, not endorsements. Full data notices ship with the
backend at app/data/FOOD-DATA-NOTICES.md.

The adapted AFCD F009805 entry is © Food Standards Australia New Zealand,
Australian Food Composition Database Release 3, and distributed under the
AFCD Data User Licence Agreement:
https://www.foodstandards.gov.au/science-data/monitoringnutrients/afcd/datauserlicenceagreement
(based on CC BY-SA 3.0 Australia).
Selected fields and translated Chinese description; 383 kJ converted using
4.184 kJ/kcal. Based on Australian data, which may not suit other countries.
The per-entry source_info preserves the required limitation statement and
licence link. The licence applies to the adapted data, not independent app code.
