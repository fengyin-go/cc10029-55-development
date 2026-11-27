/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
  /** 登记弹窗使用的字段；缺省用 fields（有些模块部分字段由后端带出） */
  createFields?: string[]
  /** 列表按"查看身份"过滤，只能看到自己的事项 */
  viewerScoped?: boolean
  /** 动作需要补充说明时的输入提示（如改派导师要填新任导师） */
  actionRemarks?: Record<string, string>
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
