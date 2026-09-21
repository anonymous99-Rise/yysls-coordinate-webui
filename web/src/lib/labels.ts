/**
 * labels.ts —— 人话文案表
 *
 * 数据里全是英文小写的机器值（flag 名、categorySource 名），
 * 这里统一翻成人能读懂的中文，顺便解释「这条标记是什么意思」。
 */

export interface FlagInfo {
  label: string
  desc: string
  kind: 'info' | 'warn'
}

export const FLAG_INFO: Record<string, FlagInfo> = {
  duplicated: {
    label: '重复出现',
    desc: '同一坐标在原始文件里出现不止一次。有一部分是导出工具靠重复 N 次实现「打完传送回来再打」的刷怪循环，不是脏数据。',
    kind: 'info',
  },
  cross_file: {
    label: '多文件收录',
    desc: '同一个点被 2 份以上原始文件收录。合并时只保留一条，重复次数留在 repeat 里。',
    kind: 'info',
  },
  placeholder_name: {
    label: '占位名',
    desc: '原始名称是「示例」「未备注地点」这种没有信息量的占位符。分类改成看它出自哪份文件来判。',
    kind: 'warn',
  },
  name_missing: {
    label: '缺名称',
    desc: '原始行只有 x,y,z 三个字段，没有名称。全库只有 1 条，导出时会补成「未备注地点」。',
    kind: 'warn',
  },
  co_located_with_other: {
    label: '同坐标多名',
    desc: '同一个坐标上挂着不同的名称。原始作者在注意事项里提过：坐标软件没刷新时会导出一堆一模一样的坐标，这类点要实地确认。',
    kind: 'warn',
  },
  region_inferred: {
    label: '区域靠坐标推断',
    desc: '名称和路径里都没有区域线索，区域是按坐标位置判的。',
    kind: 'info',
  },
  region_nearest: {
    label: '区域借最近邻',
    desc: '落在清河/开封的重叠带里判不出来，借用了最近的已知区域点位。',
    kind: 'info',
  },
  glued_split: {
    label: '粘连行已拆',
    desc: '原始文件里两行粘成了一行（少了个换行），已自动拆成两个点位。全库 3 处。',
    kind: 'info',
  },
  stray_prefix: {
    label: '多余前缀已剥',
    desc: '坐标前多了「3-」这类手滑前缀，已剥掉。',
    kind: 'info',
  },
  whitespace_delimited: {
    label: '空格分隔',
    desc: '该行用空格而不是逗号分隔字段。',
    kind: 'info',
  },
  name_contains_comma: {
    label: '名称含逗号',
    desc: '名称里带了 ASCII 逗号，解析时按「前 3 段是坐标、其余全是名称」处理。',
    kind: 'warn',
  },
  suspicious_range: {
    label: '坐标越界',
    desc: '坐标超出了合理范围，可能需要人工核对。',
    kind: 'warn',
  },
}

export const CATEGORY_SOURCE_LABEL: Record<string, string> = {
  dict: '关键词命中',
  pattern: '规则模式命中',
  path: '按来源文件判定',
  person: '人名启发式',
  placeholder: '占位名（原名无信息）',
  fallback: '没归上类',
}

export const REGION_SOURCE_LABEL: Record<string, string> = {
  name: '名称里有区域词',
  place: '按地名判定',
  path: '按来源路径判定',
  coord: '按坐标位置判定',
  nearest: '借最近邻区域',
  '': '未知',
}

export const LAYER_LABEL: Record<string, string> = {
  地表: '地表',
  地下: '地下（Z < -150）',
  深层地下: '深层地下（Z < -400，鬼市一带）',
  高台: '高台（Z > 120）',
}

/** 数据里常见但不自解释的字段，也在「数据」页里列一遍 */
export const STAT_LABEL: Record<string, string> = {
  rawFiles: '原始文件数',
  rawRows: '原始坐标行数',
  points: '规范化点位数',
  redundantRows: '被聚合掉的冗余行',
  issues: '解析问题条数',
  noteFiles: '说明文档数',
  crossFilePoints: '被多份文件收录的点',
  coLocatedPoints: '同坐标多名的点',
  regionUnknown: '区域未判定的点',
  regionFilledByNearest: '其中靠最近邻补全',
}
