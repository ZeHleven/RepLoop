// Taro's H5 renderer does not mount WeChat's native custom-tab-bar entry.
// This host is compiled only for H5 inspection, never mounted by the WeChat app.
import {useEffect,useState} from 'react'
import CapsuleTabBar from '../custom-tab-bar/index'
import {tabIndex} from '../core/capsule-navigation'
export default function H5CapsuleHost () {
  const read=()=>typeof window!=='undefined'&&tabIndex(window.location.hash.replace(/^#/,''))>=0
  const [visible,setVisible]=useState(read)
  useEffect(()=>{const update=()=>setVisible(read());window.addEventListener('hashchange',update);return()=>window.removeEventListener('hashchange',update)},[])
  return visible?<CapsuleTabBar/>:null
}
