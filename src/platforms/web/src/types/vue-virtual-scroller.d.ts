declare module 'vue-virtual-scroller' {
  import { DefineComponent } from 'vue'

  interface DynamicScrollerProps {
    items: any[]
    minItemSize: number
    keyField?: string
    direction?: 'vertical' | 'horizontal'
    listTag?: string
    itemTag?: string
  }

  interface DynamicScrollerItemProps {
    item: any
    active: boolean
    index?: number
    sizeDependencies?: any[]
  }

  export const DynamicScroller: DefineComponent<DynamicScrollerProps>
  export const DynamicScrollerItem: DefineComponent<DynamicScrollerItemProps>
  export const RecycleScroller: DefineComponent<any>
}
