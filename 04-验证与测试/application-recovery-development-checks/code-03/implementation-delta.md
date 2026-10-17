# OPS应用恢复代码03：AR03 / AR04整改

本候选只用于离线复核及代码静审；没有创建PG/Docker资源、执行SQL或访问OSS。code02 5f859286…/53离线、架构AR03原ASGI反例和AR04未PG实证的风险判断均保留；01、协议02、generic/native、其他Owner文件不改。正式应用恢复与生产RPO/RTO仍未验收。

## AR03：一个成功创建命令与一个新UUID

创建入口在异步锁内绑定成功命令的fingerprint，覆盖幂等键和认证cookie/Origin/CSRF上下文；body仍须完全匹配测试时固定的唯一创建输入。首次201从实际响应解析并绑定新UUID，之后只允许该原成功命令精确重放。新键、不同会话、不同CSRF、重复authority header均在进入真实应用前423；手工bind_new_bid后没有已知成功命令时所有创建均拒。不会把wrapper再次bind失败当成已阻止第二次SQL创建。

创建结果未知/异常/5xx时保留原命令身份，只允许该命令对账重试，不释放给新键；真实401/403/422拒绝未创建时才释放未成功槽位。原命令重放仍交实际API幂等控制面，不用harness缓存201假充真实API幂等。若真实API同键返回不同UUID则失败而非掩盖。API真实演练路径现还将发送第二key检查423，reader前后必须仅增加1条bids且原历史/旧queue/model_calls不变。

新增独立纯ASGI回归：架构原“先bind(A)再POST新键”步骤423；首次201→原键同UUID重放→新键423；换会话/CSRF拒；两个不同键并发只一个进入应用；认证/CSRF拒绝不占成功槽；合成“提交后丢响应”只准原键回读。这里的ASGI内层为显式测试桩，不把它称作PG真实新增项目或真实CSRF/登录验收；后者须放行后真实API演练。

## AR04：初始管理员不重分配，临时非pinned DDL角色

初始POSTGRES_USER只用于本次新集群基础设施、创建本次新账号/DB和最终撤销临时权限，绝不进入REASSIGN。它保有新集群默认postgres/template0/template1数据库、pg_default/pg_global表空间及builtin plpgsql的ownership。

同次新初始化另创建独立随机temporary DDL/restore LOGIN，暂时SUPERUSER仅用于已审15份SQL与untrusted vector扩展/完整restore；不进入业务profile或API子进程。必须不等于初始管理员、OID>=16384、无系统pin、当前处于明确临时super/login窗口；source SQL和target pg_restore都使用这个角色。本次随机新DB的owner最初即这个临时角色，其shared ownership必须严格等于“仅本次新DB”；拥有任意系统默认DB/表空间或其他DB都拒绝。

DDL完成后只REASSIGN这个非pinned临时角色到最终NOLOGIN非super archive owner。前后冻结并真实核对初始基础设施六项owner不变；vector/业务schema/函数/ACL/default ACL按原归档完整比对。最终临时DDL必须无任何剩余owner/ACL依赖，随后由基础设施管理员置NOLOGIN、NOSUPERUSER、所有高权flag false、PASSWORD NULL，清空本地已完成收据里的临时口令。不得为真实失败改pg_catalog、给最终owner SUPER或在既有库补权。

source/target的builtin plpgsql分别仍归各自初始基础设施账号，名字因新集群不同而不同。因此先严格核实际owner就是本次infra，再仅该builtin extension做受控身份映射；源的实际infra六项清单与原角色标签另存manifest，目标实际清单与唯一被改属新DB另存receipt。不把其他扩展/任意owner映射成同一个值，vector owner仍精确比对NOLOGIN archive owner。原模板/系统对象不属于要从source转交给业务owner的资产。

新增离线角色/SQL调用模拟覆盖：误用初始管理员0查询拒绝、OID10/pin/无临时super/login拒绝、允许新建OID角色、REASSIGN语句只指临时role且shared集合仅本次库、未知系统DB/表空间拒绝、基础设施owner变化拒绝、无剩余依赖后demote/NOLOGIN/清空口令。它们只验证实现协议和拒绝顺序，不能证明PG实际REASSIGN或extension正向已过；真实新集群完整建库/恢复仍待本候选静审放行。

## 不变边界

固定07 308字节、所有生产import来源/无live fallback、单API DSN的-I子进程、专用backup reader、业务API cert42501、旧queue/unknown不续跑、work_paused、新OSS前缀、1600/900MiB磁盘门和不pull镜像均保留。新Word/score等组合必须另审精确输入，不据本候选承接未纳入源码。CPU资产只读hash/Word sandbox/生产容量仍分别pending。
