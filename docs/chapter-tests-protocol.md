# 共享课章节测试协议与验证范围

核对日期：2026-10-20。以下说明对应当前实现，不代表平台承诺长期兼容。

## 官方来源

- 页面：https://onlineexamh5new.zhihuishu.com/stuExamWeb.html
- API封装与请求加密：https://onlineexamh5new.zhihuishu.com/static/js/stuExamWeb.3577f5621cd754d97d2e.js
- 测试列表：https://onlineexamh5new.zhihuishu.com/static/js/9.c58d6fa5a5050724a831.js
- 作业试卷与逐题保存：https://onlineexamh5new.zhihuishu.com/static/js/1.184ad30c22bdba34ce6c.js
- 动态密钥与加密：https://base2.zhihuishu.com/js/2.js

## 请求流程

1. 沿用本程序登录方式及账号 Cookie，访问考试系统 CAS 服务重定向。
2. 用官网公开 RSA 密钥将 `{"module":3}` 加密，GET `/app-commserv-user/c/has?uid=...`。
3. 取 `rt.sl`，用公开密钥恢复并校验 PKCS#1 type-1 信封，读取 `cKey`。密钥仅保存在内存。
4. 请求对象用 UTF-8 JSON、PKCS#7、AES-CBC 编码，固定 IV 与官网一致；表单只含 `secretStr`。
5. API主机固定为 `studentexam-api.zhihuishu.com`，前缀为 `/studentExam/gateway/t/v1/`。

当前CAS可能停在考试系统自己的用户管理回调HTML，而不是回到试卷页面。程序随后请求 `/studentExam/gateway/f/v1/ulogin/getLoginUserInfo`，核对返回账号与Cookie账号一致；不凭最终页面标题或URL宣称登录成功。

## 已确认的接口与字段

| 接口 | 用途与字段 |
| --- | --- |
| `exam/getSingleRecruit` | `recruitId`，取得考试系统课程信息 |
| `exam/getLoginSchoolInfo` | 获取登录学校标识 |
| `student/getStudentHomework` | `recruitId,courseId,flag=1,pageNum=0起,pageSize=100`；读取 `rt.studentHomeworkList`，`next=-1`结束 |
| `student/doHomework` | `recruitId,courseId,examId,studentExamId,schoolId`；读取 `rt.examBase.workExamParts[].questionDtos` |
| `answer/hasAnswer` | `recruitId,examId,stuExamId`；`rt=0`无答案，`rt=1`再读取答案 |
| `answer/getStuAnswerInfoNew` | `recruitId,courseId,examId,stuExamId,schoolId,questionIds`；返回按题目 `eid` 索引的答案 |
| `answer/saveStudentAnswer` | `recruitId,stuExamAnswer`；后者为JSON数组字符串，每次仅含一题 |
| `answer/submit` | 显式选择提交模式后使用`recruitId,examId,stuExamId,achieveCount`，`rt.statu=0/1`表示接口接受；随后回读试卷`state=3/4`确认 |

单题数组元素含 `examId,recruitId,stuExamId,eid,schoolId,deviceId,examType,fromType=3,answer,dataIds,questionType`。选项使用服务端 ID，多选以逗号连接，不使用字母序号。`deviceId`、`examType` 的空字符串与官网 sessionStorage 为空时的默认值一致；若服务器拒绝，流程停止，不捏造验证信息。

题型 `1` 为单选、`2` 为多选、`14` 为判断；题干字段为 `name`，选项字段为 `questionOptions[].id/content`。子题字段为 `questionChildrens`；解析时保留父题阅读材料。

保存必须同时满足业务状态 `status=200`、无错误 `code`、`rt.statu=1`，随后重新读取本题答案并与预期选项集合比较。HTTP 200 不能单独证明保存成功。

仅保存模式不调用最终提交接口；选择保存并提交时，先再次核对全部服务器答案和题数，再提交一次并读取试卷状态。不调用重做、互评、查看正确答案、修改考试时长或验证绕过接口。导出试卷会访问官网同名的 `doHomework` 接口，可能产生正常的打开试卷记录。

## 验证与尚未完成的联调

- 在线取回动态密钥成功，只检查返回结构与密钥长度，不记录密钥值。
- Python AES结果与官网原始 CryptoJS 实现的 UTF-8 测试向量一致。
- 离线测试覆盖密钥信封、加密请求、分页、题目解析、答案ID校验、逐题保存与回读、已有答案保留、超时与失败中止。
- 用户扫码刷新了同一账号会话，已通过考试系统接口核对账号身份，并取得“跨文化英语”的12项未交测试/作业列表，其中11份为章节测试，另有一项名为`test`。
- 已用真实`doHomework`响应拉取并导出绪论测试1道纯文字单选题，确认章节标识、题干、加密题目ID与选项ID；该题原先没有服务器答案。
- 原知到AI请求返回HTTP200但业务码509。已修复原解析器静默返回空值的问题，错误会明确显示并中止；用户选择改用自己配置的DeepSeek接口。
- 用户在本地隐藏输入密钥后，以`deepseek-v4-flash`实际调用成功；绪论测试1题已由模型生成选项、调用`saveStudentAnswer`保存，并由`getStuAnswerInfoNew`回读确认完全一致。此结果证明保存链路，不证明答案评分正确；没有调用交卷接口，其余章节测试没有运行。
- 最终离线回归96项通过，两个新增BAT及原启动器的环境/语法检查通过。本次密钥与临时扫码材料不写入公开说明或测试夹具；多余的临时登录Cookie、二维码及token副本已清理。

## 界面与返回流程修复（2026-10-20）

- 交互入口增加持续菜单循环；查看列表、导出成功、保存成功和错误结果均有明确返回按钮，不再直接落到BAT暂停退出。
- 统一章节页面、AI配置、启动器操作结果的面板风格；长菜单自动滚动，列表和完整文件路径分页显示，Esc逐级返回。
- 主菜单加入章节测试与AI配置；飞书、滑块和AI配置快捷BAT返回后进入主菜单，普通显式命令行模式仍保留原用法。
- 离线回归104项通过，五个快捷BAT的`--check`通过，所有BAT保留UTF-8与CRLF。
- 在Windows真实PTY中运行快捷入口，验证AI配置返回主菜单，再进入“跨文化英语”读取列表、翻页、导出绪论题目，并从结果页依次返回章节操作、课程列表、主菜单，最后正常退出（退出码0）。本次导航验证没有调用AI或保存新答案。

## 批量处理与可选交卷（2026-10-20）

- 单份/批量结果返回测试选择页；完成方式选择也可以取消回到测试列表。
- 新增一键全做与仅保存/保存并提交选择；批量范围限当前课程的普通章节测试，跳过不适用项目并记录原因，遇到不确定提交状态立即停止。
- 提交字段与`rt.statu=0/1`依据官网作业页面的`autoSub/submit`实现。保存后再次回读全部答案，核对题数，再发送一次提交请求；回读`state=3/4`后才记录`submitted=true`。发送后超时或无法确认则记录`submitted=null`，不自动重发。
- 提交分支用离线模拟验证，不在开发验证中替用户实际交卷。

## 启动入口整合（2026-10-20）

- Windows 仅保留 `一键启动.bat`；章节测试、AI 配置、飞书配置与测试、滑块验证测试全部通过主菜单进入，移除重复的独立 BAT。
- 更新 README 的统一入口、完整菜单与重开窗口说明。离线回归 118 项通过，启动器 `--check`、真实终端主菜单及退出操作检查通过。
