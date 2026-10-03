# -*- coding: utf-8 -*-
"""
build_kb_from_wechat.py — 把公众号文章转成「数字分身知识库」格式文档。

为什么不能直接把排版稿入库：
  原稿是给人看的：有封面/插图链接、YAML frontmatter、编号标题、表格、
  移动端分隔线。直接入库会被切成大量「孤儿块」（缺主题、缺版本、答非所问）。

本脚本做的四件事：
  1) 去版式噪音：YAML 头、图片链接（转成配图说明）、分隔线、粗体/代码块/emoji；
  2) 加面包屑：每个标题前缀【水利一张图APP·<篇名>】，让每个 500 字分块自带主题上下文；
  3) 表格展开：把 |版本|变更| 这样的行，转成自解释句（版本 recall 才准）；
  4) 补答疑资产：每篇增加「概览摘要 + 关键词 + 常见问题速查(问/答)」，并按 slab 生成总索引。

产出目录 kb_src/，随后用 knowledge_base.ingest_paths 增量入库。
"""
import os
import re
import sys

SRC_DIR = r"D:\Users\Claw\公众号草稿_v394"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb_src")
APP = "水利一张图APP"
VER = "v3.94（内部版，构建 2026-09-22）"

# 每篇的加工配置：源文件名、篇名、分类、关键词、摘要、常见问题
ARTICLES = [
    {
        "src": "01_全部菜单项功能详解.md",
        "out": "KB01_菜单功能详解.md",
        "title": "全部菜单项功能详解",
        "cat": "功能手册",
        "table_prefix": "版本无关·要点",
        "keywords": "菜单、子菜单、查询、筛选、获取坐标、添加照片、添加建筑物、导入导出、ovkmz、ovobj、kmz、obj、mdb、矢量数据、数据维护、传输共享、运行维护、备忘录、智能AI、设置、信息与帮助",
        "summary": "按「基本功能→扩展功能→高级功能」三层，逐项讲清每个菜单/子菜单的作用、使用方法、注意事项与常见问题，并附软件特色、四端安装与下载地址。适用版本 %s。" % VER,
        "faqs": [
            ("水利一张图 APP 主要能做什么？",
             "离线优先的地图应用：在天地图底图上管理全部水工建筑物，支持查询、筛选、增删改、多照片、测距、导航、周边搜索、导入导出、运行维护与智能 AI；安卓 / Windows / 统信 UOS / 苹果四端同源。"),
            ("怎么快速找到某一处建筑物？",
             "用顶部「查询」按名称关键词搜索，或用「筛选」按管理处 / 管理所 / 管理站 / 建筑物类型组合筛选；命中 1 个直接飞过去，命中多个则全部圈选并自动居中。"),
            ("查询和筛选有什么区别？地图上怎么区分？",
             "查询按关键词做包含匹配，地图上用绿色虚线圈标示；筛选按组织层级与类型组合，用浅蓝虚线圈标示。两者可叠加，导出数据时默认只导筛选结果。"),
            ("怎么获取某个点的经纬度？是什么坐标系？",
             "三种方式：读取当前 GPS 坐标、点选地图任意位置、按建筑物名称搜索坐标。输出为 WGS84 / CGCS2000 经纬度，与奥维、天地图一致，可直接粘到其他系统使用。"),
            ("支持导入导出哪些数据格式？",
             "导入：ovkmz / ovobj / kmz / kml / obj、矢量 mdb、Excel / CSV、照片 ZIP / 7z；导出：obj 文本坐标、ovkmz、建筑物表格（字段可选）、照片（按管理所分目录）、矢量 CSV / ovkmz。"),
            ("批量导入照片后显示 0 张怎么办？",
             "多为 ZIP 中文文件名乱码或目录层级被截断；程序已内置 GBK / GB18030 编码探测与完整相对目录修复，仍异常请改用本地单张导入或重新打包后再试。"),
            ("为什么导出的表格只有一部分数据？",
             "筛选状态会一直保持，导出默认只导出当前筛选结果。需要全量导出台账时，请先把筛选勾选项清除再导出。"),
            ("矢量 mdb 怎么导入？支持什么坐标系？",
             "菜单「矢量数据管理 → 导入矢量 mdb 数据」，通用读取 ESRI Personal Geodatabase 的面 / 线 / 点要素，自动识别 CGCS2000 高斯-克吕格带号并转换为 WGS84 经纬度。"),
            ("换手机或换电脑怎么把数据搬过去？",
             "用「数据维护 → 升级数据导出」把全部数据（含照片）打包成一个备份文件（实测约 1.39MB），新设备用「升级数据导入」恢复即可，也可用同为 WiFi 下的「传输与共享」互传。"),
            ("巡视（运行维护）记录怎么用？",
             "在「运行维护」里先设默认出发位置、建运行维护计划（日常检查 / 例行维护 / 故障处理 / 应急响应），再选中建筑物记录巡查时间、现场情况并配上照片，巡视路线自动累计，年底可直接汇总。"),
            ("智能 AI 功能在哪里开启？",
             "菜单「设置 → 智能 AI 设置」配置模型（OpenRouter / OpenAI 兼容 / 本地模型）与调用策略，再用「一键显示智能 AI 子菜单」把入口显示出来。"),
            ("删除了建筑物或照片还能恢复吗？",
             "不能。删除操作（含矢量数据清空）不可恢复，务必先做「升级数据导出」备份；「恢复初始数据」会把整库还原到随包状态，没有十足把握不要点。"),
            ("忘记内部版访问口令怎么办？",
             "请联系科技推广中心处理。口令自 v3.66 引入，v3.74 起升级为 AES-GCM（PBKDF2）加密存储。"),
            ("当前版本是多少？在哪里看？",
             "当前为 %s。版本号在「信息与帮助 → 关于」查看，历史变更在「版本变更」，数据统计在「统计」（全库 557 处：点状 407、线状渠道 150 条）。" % VER),
            ("天地图底图加载不出来怎么办？",
             "多数是密钥被改动。设置里的天地图密钥采用三层回退（本地自配 → 环境 → 内置默认），请勿随意修改，改错会导致底图无法加载。"),
        ],
    },
    {
        "src": "02_应用中的提示词梳理.md",
        "out": "KB02_智能AI与提示词.md",
        "title": "智能 AI 提示词梳理",
        "cat": "AI配置",
        "table_prefix": "要点",
        "keywords": "智能AI、系统提示词、systemPrompt、本地辅助、联网查询、数据补全、JSON补丁、联网核实、Hermes自我学习、占位示例、ai_seed、API Key、知识库种子、OpenRouter、failover、五级组织",
        "summary": "拆解 APP 内置的智能 AI：三态系统提示词（人设）、数据补全 / 联网核实 / Hermes 自我学习三类任务提示词、三态占位示例、本地密钥种子 ai_seed.js 与随包知识库种子，全部指向源码 ai_module.js。",
        "faqs": [
            ("智能 AI 有哪几种工作模式？",
             "两档开关组合出三种形态：内部资料（水利 / 感知）默认「本地辅助」——不联网、只基于本地记录；也可逐次开启「联网在线查询」；古建类默认开放联网核实。"),
            ("为什么内部资料默认不联网？会不会乱编？",
             "因为公开大模型没有单位内部建筑物的准确参数与坐标，联网容易张冠李戴。系统提示词明确禁止编造或臆测内部参数，只允许基于本地记录分析、补全缺失字段、标注存疑项。"),
            ("系统提示词写在哪里？",
             "在 ai_module.js 的 systemPrompt() 中定义，按「是否联网」与「应用域」返回三态人设；三端共用同一份 ai_module.js，不会出现串味。"),
            ("智能更新（数据补全）返回什么格式？",
             "只返回 JSON 补丁：{\"patch\":{\"字段\":\"新值\"}}，可更新字段限定在给定清单内。系统先列出当前记录字段（空值标「（空）」），模型返回后先预览再写入，避免误写。"),
            ("个人 API Key 放在哪里？安全吗？",
             "放在本地文件 ai_seed.js（window.AI_SEED），不进共享源码。内置三个 OpenRouter 免费模型预设，个人 Key 请勿公开分享或提交到共享仓库。"),
            ("内置免费模型有哪些？被限流怎么办？",
             "MiniMax M2.7 / GLM 5.2 / Nemotron 3 Nano Omni（均为 :free、OpenAI 兼容）。默认 failover 策略，上游限流时自动切换下一个模型，避免单模型 429 中断。"),
            ("可以接自己的模型吗？",
             "可以。设置 → 智能 AI 设置，添加 OpenRouter / OpenAI 兼容 / 本地部署模型，填写引用地址、模型 ID、API Key，可设默认模型与自动调用策略（仅默认 / 失败切换 / 轮询），支持连接测试与配置导入导出。"),
            ("不联网时智能助手靠什么回答？",
             "靠构建期预生成、随包内置的知识库种子：水利 557 条 / 感知 1125 条 / 古建 1032 条，每条含属地、简介、特点等通用字段，启动时合并进本地知识库。"),
            ("Hermes 自我学习是做什么的？",
             "每次智能操作后异步触发，从本次操作中抽取关键参数与操作偏好（不超过 6 条要点），以 hermes 标签写入知识库，后续查询时作为上下文注入，形成「查询 → 更新 → 纠错 → 学习」闭环。"),
            ("什么是五级组织？",
             "局 → 管理处 → 管理所 → 管理站 → 管理段。当用户问「某管理处下属多少所 / 站 / 段」时，模型会结合组织清单与实际数据按这五级回答。"),
            ("知识库支持哪些外部资料入库？",
             "菜单「知识库管理」支持 md + json 混合库，读取 pdf / xls / doc / csv / txt / md / 网页 / 公众号 / 微博等入库，支持导出 zip 备份、导入备份与一键重新播种。"),
        ],
    },
    {
        "src": "03_APK版本变更记录.md",
        "out": "KB03_版本变更记录.md",
        "title": "版本变更记录",
        "cat": "版本历史",
        "table_prefix": "版本变更",
        "keywords": "版本变更、v3.29、v3.48、v3.50、v3.88、v3.92、v3.94、奇偶双通道、内部版、公开版、port、端口劫持、版本一致性、version.json、APP_VERSION",
        "summary": "从 v3.29 到 %s 的每个版本改了什么，并展开四个关键节点：v3.48 致命回归、v3.50 奇偶双通道发版、v3.88~v3.92 跨平台与端口三连修、v3.94 版本一致性治理。" % VER,
        "faqs": [
            ("当前最新版本是多少？",
             "当前为 %s。安卓 / Windows / 统信 UOS / 苹果四端使用同一版本号。" % VER),
            ("在哪里能看到版本变更记录？",
             "APP 内「信息与帮助 → 版本变更」；公众号另有图文版《APK 版本变更记录》。"),
            ("偶数版和奇数版有什么区别？",
             "v3.50 起实行奇偶双通道：偶数版保留真实水利数据（坐标 / 管理所 / 设计参数）并标注「内部版」；奇数版为脱敏公开测试版。"),
            ("v3.48 为什么打开就崩？",
             "data.js 中两条记录之间缺了一个逗号，整个数据字面量解析失败导致初始化即崩。修复后新增 node --check + JSON.parse 双门禁（557 条校验）并全平台重建。"),
            ("v3.88 到 v3.92 主要修了什么？",
             "跨平台与端口三连修：① Windows / 鸿蒙导入卡在「请选择」；② 统信 UOS 导入 mdb 报 Unexpected token '?'（ES2020 语法）；③ 端口劫持与三端互抢（改读 /proc/net/tcp 直杀残留进程 + 三端独立端口 7205 / 7206 / 7207）。"),
            ("v3.94 具体做了哪些改动？",
             "锁 APP_VERSION 与 version.json 一致（version.json 作为唯一真相源）、补齐 v3.67–v3.92 缺失的变更记录、把矢量 mdb 导入导出与「请选择」避坑经验沉淀为需求文档＋智能体提示词、重出三端四平台部署包。"),
            ("以前「关于」页版本号显示不对是什么原因？",
             "APP_VERSION 常量长期停在 3.90，与 version.json、AndroidManifest 三源没有锁版本。v3.94 起三源一致，构建脚本由 version.json 同步 versionCode / versionName。"),
            ("升级安装要注意什么？",
             "建议先卸载旧版 APK 再装新版，避免缓存导致功能异常；苹果端需用 Safari 打开托管地址添加到主屏幕，微信内打开不可用。"),
            ("三图版本号为什么总是一起变？",
             "水利一张图与「水利感知项目一张图」「古建景点打卡」同期发版、版本号同步治理，便于一次性回归验证与同步检查。"),
        ],
    },
    {
        "src": "04_开发踩坑记录.md",
        "out": "KB04_开发踩坑与故障排查.md",
        "title": "开发踩坑与故障排查",
        "cat": "故障排查",
        "table_prefix": "要点",
        "keywords": "踩坑、故障排查、打开即崩、白屏、Script error、@0:0、defaultOffice、形参遮蔽、polyfill、UOS deb、ar补齐、E_ACCESSDENIED、请选择、ES2020、mdb_reader、端口劫持、ZIP乱码、escapeJson、版本漂移",
        "summary": "17 个真实踩过的坑，每个都给出「现象 → 根因 → 教训」，覆盖数据文件崩溃、未定义函数、ES6 兼容、deb 打包、Windows 权限、跨平台「请选择」、mdb ES2020 转译、端口劫持与版本号漂移，可直接当排障手册用。",
        "faqs": [
            ("APP 打开就崩或白屏，最常见原因是什么？",
             "多为数据 / 脚本解析失败：data.js 记录间缺逗号、提示字符串里有未转义的双引号，都会让初始化阶段抛异常。现在的门禁是修改 data.js 必须通过 node --check 与 JSON.parse 双重校验。"),
            ("点击菜单报「运行错误：Script error. @0:0」怎么排查？",
             "这类报错没有行号，通常是脚本加载失败或引用了未定义函数（如导出曾调用从未定义的 defaultOffice()、ai_module.js 形参 q 遮蔽了 DOM 助手 q）。需读源码定位，并用 Node 忠实沙盒实际调用函数做执行级验证。"),
            ("统信 UOS 安装 deb 失败或提示文件损坏？",
             "原因是构造 tar 时只写文件、未声明父目录条目，以及 ar 成员大小未按偶数补齐。现已加打包门禁：写文件前回退生成全部祖先目录条目、ar 头空格填充与大小补齐。"),
            ("Windows 端安装后打不开或提示无权限？",
             "WebView2 的 userDataFolder 指向无权限目录会报 E_ACCESSDENIED。出包前强制校验 exe 含 GetWebView2DataFolder 修复标记，缺失直接拒包。"),
            ("导入文件时明明选好了却一直提示「请选择」？",
             "Windows / 鸿蒙桌面壳注入的 window.Android 只实现了「选文件」，没把文件内容回传。现已统一：仅安卓原生桥走桥，其余平台一律走页面 input type=file + FileReader。"),
            ("统信 UOS 导入矢量 mdb 报 Unexpected token '?'？",
             "mdb_reader.js 含 ES2020 语法（?? / ?.），旧 WebEngine 解析不了。已用 Babel 转译为 ES5 并补全 process / window 垫片，另加防御守卫：仍是未转译旧版则跳过注入走回退链。"),
            ("升级安装后打开的还是旧页面、新功能看不到？",
             "这是端口劫持：旧实例长期占用端口，持续服务旧 webroot。释放端口不再依赖 ss / fuser，而是读 /proc/net/tcp 找到监听端口的 inode 再反查进程杀掉，保证每次都从当前 webroot 起服务。"),
            ("水利 / 感知 / 古建三端装在同一台设备上互相抢端口？",
             "v3.92 起每端独立端口：水利 7205、感知 7206、古建 7207；配合 postinst 以 root 走 /proc 直杀残留服务 + 启动器 / 卸载双清理，三端同机共存不再撞端口。"),
            ("批量导入照片 ZIP 后照片数为 0？",
             "ZIP 条目中文名多为 GBK / GB18030 编码（7-Zip 常见），且文件夹层级曾被截断。现已做字符集依次探测并改用完整相对目录归档。"),
            ("老设备上部分功能崩、部分功能正常？",
             "系统 WebView 版本过低（如 Chrome 37）缺少 Array.find / Object.assign / TextDecoder 等 ES6 API。已在 app.js 与桥文件顶部统一补 polyfill（含手写 utf8 兜底）。"),
            ("ovobj 导入或导出报 script error？",
             "escapeJson 只转义了双引号、漏了单引号，注入 KML 属性时破坏结构。JSON 注入到 XML / 属性时单双引号都要转义。"),
            ("统信 deb 这么多架构，该选哪个包？",
             "龙芯 3A4000 选 mips64el；龙芯 3A5000 及以上选 loongarch64；飞腾 / 鲲鹏选 arm64；x86 选 amd64。双击安装或用 dpkg -i 均可。"),
            ("注释和代码不一致会造成什么问题？",
             "例如注释写「此处不立即 cleanInbox」但函数体却调用了它，导致三个 getElementById 守卫全部为 null。「注释说不调用却调用」属高危信号；且 DOM 守卫清理必须在 innerHTML 写入之后调用。"),
        ],
    },
    {
        "src": "05_变更过程记录.md",
        "out": "KB05_变更过程与迭代历程.md",
        "title": "变更过程与迭代历程",
        "cat": "开发历程",
        "table_prefix": "要点",
        "keywords": "变更过程、迭代历程、三次回归、忠实沙盒、执行级验证、门禁、真机复现、多副本同步、三图同源、四端同源、v3.46、v3.48、v3.94、抽包核验",
        "summary": "不只是一张版本表，而是每个版本变化背后的复现、回归与较真：从「能跑到不崩」的第一道坎、v3.46 三次回归、把门禁焊进发版、v3.48 深夜致命回归、跨平台与端口隐蔽问题，到 v3.94 把版本号这件小事做对。",
        "faqs": [
            ("这个项目一路经历了哪些阶段？",
             "从 v3.29 到 v3.94 大致四段：基础功能补齐 → 智能 AI 与知识库深度融合 → 跨平台与端口治理 → 版本一致性收敛。"),
            ("什么叫把门禁「焊死」？",
             "门禁不是写在文档里给人看，而是嵌进发版脚本能真正阻断：data.js 双校验、忠实沙盒执行级验证、UOS deb 打包门禁、多副本同步检查全绿才放行。"),
            ("什么是忠实沙盒？为什么重构建修不了源码 bug？",
             "忠实沙盒不掩码未定义全局，真实加载 data.js + bridge + app.js 并调用函数。源码级的真源 bug（如从未定义的 defaultOffice）重构建 APK 根本修不好，必须读源码定位。"),
            ("v3.46 的三次回归是怎么回事？",
             "v3.43~v3.45 一口气新增 9 项能力后用户复检称「老问题没解决」，于是三轮回归：先补明显问题，再读源码抓出形参 q 遮蔽与未定义 defaultOffice 两个真源 bug，最后源码修复 + 全端重建 + Node 沙盒执行级验证才算根治。"),
            ("三图同源、四端同源怎么保证一致性？",
             "水利 / 感知 / 古建三图同源，安卓 / Win / UOS / iOS 四端同源；同步脚本的 canonical 严格对齐构建流水线，每次发版跑同步检查 + 抽包核验，确认零分叉才交付。"),
            ("v3.94 这个「小版本」的价值是什么？",
             "补了长期欠账：三源锁版本一致、补齐 v3.67–v3.92 缺失变更记录、把矢量 mdb 与「请选择」的避坑经验沉淀为「需求文档＋智能体提示词」，让下一次不必从零摸索。"),
            ("有哪些不变的开发原则？",
             "只调顺序、不删功能（每页退出按钮 + 三击空白防卡死严禁删除）；风格靠 CSS 变量四端统一；改完必重构建、重建必核验；真机复现优先于猜测。"),
        ],
    },
    {
        "src": "06_文档沉淀与记忆.md",
        "out": "KB06_文档沉淀与记忆体系.md",
        "title": "文档沉淀与记忆体系",
        "cat": "项目文档",
        "table_prefix": "工程文档",
        "keywords": "文档沉淀、记忆体系、SRS、详细需求文件、详细提示词、三图联动、APK命名与归档、问题台账、需求与提示词、矢量MDB需求文档、智能体开发提示词、经验沉淀",
        "summary": "项目沉淀体系总览：工程文档（需求 / 提示词 / 方法论 / 规范 / 问题台账）+ 跨会话记忆体系（问题台账 / 变更文档 / 当日 memo）+ v3.94 新增的「需求文档 + 智能体开发提示词」成对专项沉淀，附可继续展开的选题清单。",
        "faqs": [
            ("工程文档有哪些？放在哪里？",
             "集中在「水利工程一张图_经验沉淀/」目录：01 详细需求文件（SRS）、02 详细提示词、03 三图联动方法论与升级规范、04 APK 命名与归档规范、05 待修复问题汇总。"),
            ("详细需求文件（SRS）包含哪些内容？",
             "项目概述、技术架构与约束、数据模型、20+ 功能模块（地图 / 标记 / 筛选 / 列表 / 增删改 / 定位 / 导航 / 测距 / 周边 / 坐标 / 照片 / 导入导出 / 统计 / 恢复 / 菜单 / 版本变更 / 帮助）、非功能需求、两日踩坑教训、发布前总检查清单、文档与备份同步规则。"),
            ("文档和记忆体系有什么区别？",
             "工程文档讲「要做什么、怎么做」，给人看、随版本更新；记忆体系讲「踩过什么坑、定了什么铁律」，给未来的自己或 Agent 看，用于防止复发。"),
            ("v3.94 新沉淀的专项文档是什么？",
             "「需求与提示词/」目录下的《矢量 MDB 导入导出·需求文档》（把 ES2020 转译、端口劫持、「请选择」、版本号漂移等根因固化为需求基线 + §4 避坑指南 + 验收标准）与《矢量 MDB 导入导出·开发提示词（智能体）》（可直接交给另一个 Agent 的提示词）。"),
            ("问题台账怎么用？",
             "每条问题一个编号，含现象 / 根因 / 修复 / 验证 / 防复发，是可检索的「错题本」；它与变更文档（版本脉络主档案）、当日 memo 共同构成跨会话记忆。"),
            ("三图联动升级规范讲什么？",
             "讲水利 / 感知 / 古建三图同源时，版本号需要在八处保持同步，以及统一的升级规范。"),
            ("APK 命名与归档规范讲什么？",
             "安装包的命名规则、归档目录结构，以及版本号奇偶通道（偶数内部版 / 奇数公开版）的发版约定。"),
            ("接下来还能展开哪些更细的文章？",
             "已列 8 个选题：需求说明书精读（20+ 模块）、四端同源详细设计、三图联动版本八处同步、APK 命名与奇偶双通道、矢量 MDB 专项精读、智能体提示词实践、问题台账 Top 坑、记忆体系实践。"),
        ],
    },
    {
        "src": "07_推广宣传水利APP.md",
        "out": "KB07_产品介绍与推广.md",
        "title": "产品介绍与使用价值",
        "cat": "产品介绍",
        "table_prefix": "要点",
        "keywords": "产品介绍、工程管理、运行维护、第三方协作、预算财务结算、巡视记录、导出台账、获取坐标、四端同源、下载方式、公众号、水利一张图、5090、内部版",
        "summary": "面向工程管理、运行维护、第三方协作、项目预算与财务结算四个岗位的实用价值说明，含四类核心能力、四平台支持情况与下载获取方式。语气朴素，适合直接转发给一线同事。",
        "faqs": [
            ("这个 APP 是干什么的？",
             "把管理处沿线的水工建筑物放到一张天地图底图上，让大家查得着、用得上、带得走：数据导入、一张图展示、随手更新、获取坐标四类核心能力。"),
            ("工程管理部门用它能做什么？",
             "家底清楚、台账齐全：全部 557 处水工建筑物（点状 407 处、线状渠道 150 条）一目了然，按名称或管理所秒级定位，桩号 / 设计流量 / 闸门型式一屏看全，导出建筑物表格可自选字段直接做台账。"),
            ("运行维护部门怎么用？",
             "先设默认出发位置，再排运行维护计划（日常检查 / 例行维护 / 故障处理 / 应急响应）；巡视时选中建筑物记录巡查时间、现场情况并配上照片，巡视路线自动累计，年底可直接汇总。"),
            ("第三方协作单位怎么接数据？",
             "用「升级数据导出」把全部数据（约 1.39MB）打包交给协作方导入，标注与照片完整转移；与外部系统交换坐标可导出 obj 文本坐标或奥维 ovkmz，拿去奥维里直接打开。"),
            ("预算财务结算用得上吗？",
             "用得上。巡视记录、照片、维护计划都带着时间和位置，结算时哪段渠道维护过、哪个建筑物拍过照有据可查；导出照片按管理所自动分文件夹，归档和报账都省事。"),
            ("支持哪些平台？怎么安装？",
             "安卓 / 鸿蒙：apk 直接装；苹果：用 Safari 打开托管地址添加到主屏幕（微信内打开不行）；Windows：msi / 一键安装 exe；统信 UOS：deb 包，适配 mips64el / loongarch64 / arm64 / amd64 四种 CPU 架构。四端功能与数据格式完全一致。"),
            ("怎么下载安装包？",
             "关注公众号「小七爱旺仔」，回复关键词「水利一张图」或「5090」，即可获取最新下载链接（含安卓 APK / Win 安装包 / 统信 UOS deb / iOS 托管包）；也可通过京办 APP 工作群、百度网盘获取。"),
            ("里面的数据敏感吗？",
             "当前为内部版，含单位内部数据，请勿外传。内部版自 v3.66 起有访问口令保护，v3.74 起口令加密升级为 AES-GCM（PBKDF2）。"),
            ("使用中发现问题怎么反馈？",
             "欢迎通过公众号「小七爱旺仔」留言提交 bug 或新的功能需求，会逐一跟进修复；建议安装后先实际跑一遍业务流程验证。"),
        ],
    },
]

RE_IMG_LINE = re.compile(r"^\s*!\[([^\]]*)\]\([^)]*\)\s*$")
RE_IMG_INLINE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
RE_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF\uFE0F]"
)
RE_STRIP_HEADING = re.compile(r"^#{1,6}\s*")


def strip_frontmatter(lines):
    """去掉 YAML frontmatter（文件开头的 --- ... ---）。"""
    if not lines or lines[0].strip() != "---":
        return lines
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[i + 1:]
    return lines


def clean_inline(line):
    """行内清洗：去粗体 / 行内代码符 / 表格竖线残留 / 多余空白。"""
    line = RE_EMOJI.sub("", line)
    # 尖括号技术写法转成书名号形式：<input type=file> -> 「input type=file」
    # 否则即使过了白名单清洗，input 仍被当作 HTML 标签删空。
    line = re.sub(r"<([^<>\n]{1,40})>", r"「\1」", line)
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = line.replace("`", "")
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line.rstrip()


def expand_table_row(line, prefix):
    """把表格行展开成自解释句：| A | B | C | -> 【prefix】A：B｜C。"""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    cells = [clean_inline(c) for c in cells if c not in ("", "---", ":---", "---:")]
    if not cells:
        return None
    key, rest = cells[0], cells[1:]
    if not rest:
        return None
    joined = "：".join(rest) if len(rest) == 1 else "｜".join(rest)
    return "【%s】%s：%s。" % (prefix, key, joined)


def convert_body(raw, breadcrumb, table_prefix):
    """把排版稿正文转成知识库正文：去图、去分隔线、标题加面包屑、表格展开。"""
    lines = strip_frontmatter(raw.splitlines())
    out, figures = [], []
    prev_was_table = False
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            continue
        if stripped in ("---", "***", "___"):
            continue
        # 整行图片：收集说明文字，不入库
        m = RE_IMG_LINE.match(line)
        if m:
            alt = m.group(1).strip()
            if alt:
                figures.append(alt)
            continue
        # 原文 H1 用 KB 标题替代
        if line.startswith("# "):
            continue
        # 代码块围栏
        if stripped.startswith("```"):
            continue
        # 页脚署名
        if "水利5090生成" in stripped:
            continue
        # 表格：首个 | 开头的行是表头（丢弃），其余行展开为自解释句
        if stripped.startswith("|"):
            if not prev_was_table:
                prev_was_table = True
                continue
            expanded = expand_table_row(stripped, table_prefix)
            if expanded:
                out.append(expanded)
            continue
        prev_was_table = False

        line = RE_IMG_INLINE.sub(lambda mm: "（配图：%s）" % mm.group(1).strip(), line)
        line = clean_inline(line)
        if not line.strip():
            continue

        if line.lstrip().startswith("#"):
            text = RE_STRIP_HEADING.sub("", line).strip()
            out.append("### 【%s】%s" % (breadcrumb, text))
        elif stripped.startswith(">"):
            out.append(line.replace(">", "").strip())
        else:
            out.append(line)
    return out, figures


def build_doc(cfg):
    """组装单篇知识库文档的文本。"""
    title = cfg["title"]
    bread = "%s·%s" % (APP, title)
    body, figures = convert_body(
        open(os.path.join(SRC_DIR, cfg["src"]), encoding="utf-8").read(),
        bread, cfg["table_prefix"]
    )

    lines = []
    lines.append("# 【%s】%s" % (APP, title))
    lines.append("")
    lines.append("概览摘要：%s" % cfg["summary"])
    lines.append("关键词：%s" % cfg["keywords"])
    lines.append("适用版本：%s" % VER)
    lines.append("来源：微信公众号「小七爱旺仔」（原文署名：水利5090生成）。%s" % (
        "配图见文末「配图清单」，原图为 APP 真机截图。" if figures else ""))
    lines.append("")
    lines.append("### 【%s】常见问题速查" % bread)
    lines.append("")
    for i, (q, a) in enumerate(cfg["faqs"], 1):
        lines.append("问%d：%s" % (i, q))
        lines.append("答%d：%s" % (i, a))
        lines.append("")
    lines.append("### 【%s】正文内容" % bread)
    lines.append("")
    lines.extend(body)
    if figures:
        lines.append("")
        lines.append("### 【%s】配图清单（原图为 APP 真机截图）" % bread)
        lines.append("")
        for f in figures:
            lines.append("- 配图：%s" % f)
    lines.append("")
    return "\n".join(lines), len(body)


def build_index(docs_meta):
    """生成总索引文档：一篇一行的摘要 + 全部 FAQ 问题的路由表。"""
    lines = []
    lines.append("# 【%s】知识库总览与索引" % APP)
    lines.append("")
    lines.append("概览摘要：本索引汇总「%s」主题下已入库的全部知识条目，用于答疑时快速定位该去哪一篇找答案。当前适用版本：%s。" % (APP, VER))
    lines.append("关键词：水利一张图、%s、%s" % (
        APP, "、".join(m["title"] for m in docs_meta)))
    lines.append("")
    lines.append("### 【%s·索引】条目清单" % APP)
    lines.append("")
    for m in docs_meta:
        lines.append("条目【%s】（分类：%s）：%s" % (m["title"], m["cat"], m["summary"]))
        lines.append("")
    lines.append("### 【%s·索引】问题路由表" % APP)
    lines.append("")
    for m in docs_meta:
        for q, _a in m["faqs"]:
            lines.append("若被问到「%s」，参见条目【%s】。" % (q, m["title"]))
    lines.append("")
    return "\n".join(lines)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    docs_meta, paths = [], []
    for cfg in ARTICLES:
        text, nlines = build_doc(cfg)
        path = os.path.join(OUT_DIR, cfg["out"])
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        paths.append(path)
        docs_meta.append(cfg)
        print("生成 %-28s 字符数=%5d  正文行=%3d  问答=%2d" % (
            cfg["out"], len(text), nlines, len(cfg["faqs"])))

    idx_path = os.path.join(OUT_DIR, "KB00_知识库总览与索引.md")
    with open(idx_path, "w", encoding="utf-8") as f:
        f.write(build_index(docs_meta))
    paths.insert(0, idx_path)
    print("生成 %-28s 字符数=%5d" % ("KB00_知识库总览与索引.md",
                                   len(build_index(docs_meta))))
    print("\n共 %d 篇知识库文档，输出目录：%s" % (len(paths), OUT_DIR))

    with open(os.path.join(OUT_DIR, "_paths.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(paths))

    if "--ingest" in sys.argv:
        print("\n开始入库 ...")
        import knowledge_base
        res = knowledge_base.ingest_paths(paths, name="myme_kb", category=APP)
        added = tot = 0
        for r in res:
            if "error" in r:
                print("  [失败] %s -> %s" % (os.path.basename(r["path"]), r["error"]))
            else:
                print("  %-28s 新增块=%-4s 总块=%s" % (
                    os.path.basename(r["path"]), r.get("added"), r.get("total")))
                added += r.get("added") or 0
                tot = r.get("total") or tot
        print("\n入库完成：新增 %d 块，知识库总计 %d 块" % (added, tot))


if __name__ == "__main__":
    main()
