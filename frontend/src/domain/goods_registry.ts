/** 商品登记中心批量归并的前端草稿状态。
 *
 * 列表里的勾选框与“批量归并工作台”共享同一份状态；允许同一商品标识
 * 被加进多个合并组——重复入组的双归属拦截由后端处理单负责。
 */
import { computed, reactive, ref } from 'vue'

export interface DraftGroup {
  localKey: number
  /** 保留档案的商品标识 */
  keep: string
  /** 组内全部商品标识（含保留档案） */
  codes: string[]
}

const pickedCodes = reactive(new Set<string>())
const draftGroups = ref<DraftGroup[]>([])
let groupSeq = 0

export function useMergeDraft() {
  const picked = computed(() => Array.from(pickedCodes))
  const groupCount = computed(() => draftGroups.value.length)

  function isPicked(code: string): boolean {
    return pickedCodes.has(code)
  }

  function togglePick(code: string, checked: boolean): void {
    if (checked) pickedCodes.add(code)
    else pickedCodes.delete(code)
  }

  function pickMany(codes: string[], checked: boolean): void {
    for (const code of codes) togglePick(code, checked)
  }

  function clearPicked(): void {
    pickedCodes.clear()
  }

  /** 把当前勾选档案建成一个合并组，默认第一个为保留档案。 */
  function createGroupFromPicked(): DraftGroup | null {
    const codes = Array.from(pickedCodes)
    if (!codes.length) return null
    return createGroup(codes)
  }

  function createGroup(codes: string[]): DraftGroup {
    groupSeq += 1
    const unique = Array.from(new Set(codes.filter(Boolean)))
    const group: DraftGroup = {
      localKey: groupSeq,
      keep: unique[0] ?? '',
      codes: unique,
    }
    draftGroups.value.push(group)
    clearPicked()
    return group
  }

  function removeGroup(localKey: number): void {
    draftGroups.value = draftGroups.value.filter((g) => g.localKey !== localKey)
  }

  function removeCode(group: DraftGroup, code: string): void {
    group.codes = group.codes.filter((c) => c !== code)
    if (group.keep === code) group.keep = group.codes[0] ?? ''
    if (group.codes.length === 0) removeGroup(group.localKey)
  }

  function addCode(group: DraftGroup, code: string): boolean {
    const value = code.trim()
    if (!value || group.codes.includes(value)) return false
    group.codes.push(value)
    if (!group.keep) group.keep = value
    return true
  }

  function resetDraft(): void {
    draftGroups.value = []
    clearPicked()
  }

  function toPayloadGroups() {
    return draftGroups.value
      .filter((g) => g.codes.length >= 2 && g.keep)
      .map((g, index) => ({
        key: `G${index + 1}`,
        keep: g.keep,
        members: g.codes.filter((c) => c !== g.keep),
      }))
  }

  return {
    pickedCodes,
    picked,
    draftGroups,
    groupCount,
    isPicked,
    togglePick,
    pickMany,
    clearPicked,
    createGroupFromPicked,
    createGroup,
    removeGroup,
    removeCode,
    addCode,
    resetDraft,
    toPayloadGroups,
  }
}
