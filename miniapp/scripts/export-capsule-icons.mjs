// Export the exact selected library icons, not new hand-drawn SVG paths.
import {createRequire} from 'node:module'
import {mkdir,writeFile,copyFile} from 'node:fs/promises'
const require=createRequire(new URL('../../../navigation-preview-20260912-r1/package.json',import.meta.url))
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server'),icons=require('lucide-react')
await mkdir('src/assets/navigation',{recursive:true})
for(const [name,key] of [['training','Dumbbell'],['nutrition','Utensils'],['agent','MessageCircle'],['me','UserRound']]) {
 for(const selected of [false,true])await writeFile(`src/assets/navigation/${name}${selected?'-selected':''}.svg`,renderToStaticMarkup(React.createElement(icons[key],{size:25,strokeWidth:1.8,color:selected?'#1d6b49':'#718074'}))+'\n')
}
await copyFile(require.resolve('lucide-react/package.json').replace('package.json','LICENSE'),'src/assets/navigation/LICENSE.txt')
