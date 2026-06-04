# Anti-patterns

These are the moves that *compile* but signal a design or correctness problem. The pattern is almost always the same: a reflexive token (`.clone()`, `'static`, `unwrap`, `let _ =`) silences the compiler or runs the code, while pushing the real cost or bug downstream. For each: **症状 → 为何坏 → better**. This is the language layer — `cargo clippy` catches the mechanical ones (`clippy::clone_on_copy`, `clippy::unwrap_used`, `clippy::let_underscore_must_use`); turn those on. This file is about the *design judgement* clippy can't make for you.

## `.clone()` 滥用掩盖设计问题

**症状**: 借用检查器报 E0382/E0502，第一反应是 `.clone()` 让它过；`String`/`Vec`/`Arc` 在热路径里反复 clone；函数签名收 `String` 而不是 `&str`。

**为何坏**: clone 不是 bug,但**反射式 clone 是在用运行时拷贝掩盖一个所有权设计没想清楚的事实**。它把"谁拥有这份数据、生命周期到哪"的问题用一份深拷贝绕过去,数据就此分叉成两份各自演化,后面读到旧值的 bug 极难查。

**better**: 先问"我需要*所有权*还是只需要*读*?"。只读 → 传 `&T`(`&str`/`&[T]`),零拷贝。需要在多处共享只读 → `Arc<T>`(clone 只是引用计数 +1,不深拷)。返回值要么借要么传所有权 → `Cow<'_, str>`(借用为主、偶尔改时才拷)。真的需要独立可变副本时才 clone,并在心里能说出为什么。

```rust
// 反射式: 收所有权只为了读
fn greet(name: String) { println!("hi {name}"); }
greet(user.name.clone());          // 被迫 clone

// idiomatic: 借用
fn greet(name: &str) { println!("hi {name}"); }
greet(&user.name);                  // 零拷贝
```

详细的 `&T`/`Arc`/`Cow`/`Box` 选型见 `ownership-borrowing-lifetimes.md`。

## 对抗借用检查器(而不是听它的)

**症状**: 用 `clone`/`RefCell`/`unsafe`/重排代码硬凑,只为了消掉一个 E0499/E0502(同时可变借用 / 可变与不可变并存)。

**为何坏**: 借用检查器报"同时存在 `&mut` 和 `&`"时,**它通常在指出一个真实的别名 + 可变性隐患**(迭代时改容器、self 的两个字段互相借用)。压下报错而不改设计,等于把编译期的别名安全检查关掉了。

**better**: 把报错当设计反馈读。常见根因修法:

| 报错场景 | 根因 | 修法 |
|---|---|---|
| 迭代 `&vec` 时想 `vec.push` | 迭代器持有 `&vec`,push 要 `&mut vec` | 先收集要加的元素,循环后再 `extend`;或 `vec.retain`/`drain` |
| `self.a` 和 `self.b` 同时 `&mut` | 整体借 `&mut self` 太粗 | **split borrow**:直接 `let (a, b) = (&mut self.a, &mut self.b);`,编译器允许不相交字段 |
| 函数中途既要读又要写同一处 | 借用活得太长 | 缩短借用作用域(NLL 已尽力),或拆成"算出新值 → 再写入"两步 |
| 真需要共享可变 | 单线程图状结构 | `Rc<RefCell<T>>`(把检查移到运行时,但这是*有意*选择,不是逃避) |

`RefCell` 不是"绕过借用检查",它是把借用检查从编译期挪到运行时——用错了 `borrow()` 仍会 panic。只有在确实需要共享可变(图、观察者、缓存)时才用。

## 到处 `'static`

**症状**: 编译器要求一个 lifetime,就到处加 `'static`(`fn f<T: 'static>`、`Box<dyn Trait + 'static>`、把字段改成 `&'static str`)直到过编译。

**为何坏**: `'static` 不是"生命周期长",而是"**能活到整个程序结束**"。反射式加 `'static` 要么 (1) 过度收紧了 API,让调用方再也不能传借用的临时数据;要么 (2) 逼你把本该是局部的数据 `Box::leak` / 全局化,造成内存泄漏或全局可变状态。

**better**: 先分清需求是哪一类:

- 函数只是借数据用一下 → 用**具名/省略 lifetime**(`fn f<'a>(x: &'a str)` 或直接 `fn f(x: &str)`),别上 `'static`。
- 真要 spawn 到线程/tokio 任务,future 必须 `'static` → 那是因为任务可能比当前栈帧活得久,正确做法是**move 进所有权**(`String`、`Arc<T>`),而不是把借用强行标 `'static`。
- trait object 默认就是 `+ 'static`,只有当它要持有借用时才需要写出 `Box<dyn Trait + 'a>`。

```rust
// 反射式: 强行 'static, 调用方再也不能传栈上 &str
fn store(s: &'static str) { /* ... */ }

// 真实需求是"任务要拥有它" → move 所有权进去
tokio::spawn(async move { use_it(s.to_owned()).await });
```

## `Box::leak` 当生命周期万能钥匙

**症状**: 需要一个 `&'static T`,于是 `Box::leak(Box::new(x))` 一键满足。

**为何坏**: `Box::leak` **永久泄漏内存**——这块分配到进程结束都不回收。在请求处理、循环、可重入路径里用,就是每次调用泄一点,稳定增长直到 OOM。它把"我没想清楚这东西该活多久"伪装成了一个能编译的 `&'static`。

**better**:
- 进程级、只初始化一次的配置/全局表 → `OnceLock<T>`(标准库,线程安全,不泄漏语义、内存随进程自然回收)或 `LazyLock<T>`(1.80+)。这才是"程序生命周期的单例"的正确表达。
- 需要共享所有权而非 `'static` → `Arc<T>`。
- 仅 main 里初始化一次、确定不再释放的引导期数据 → `Box::leak` *可以*接受(等价 OnceLock 的手动版),但要在注释里写明"intentional one-time leak"。

```rust
use std::sync::OnceLock;
static CONFIG: OnceLock<Config> = OnceLock::new();
fn config() -> &'static Config {
    CONFIG.get_or_init(Config::load)   // 真正的程序级单例,无泄漏语义
}
```

## `unwrap()` / `expect()` 进生产路径

**症状**: `req.param("id").unwrap()`、`mutex.lock().unwrap()`、`s.parse().unwrap()` 散布在请求处理 / 库 API 里。

**为何坏**: 每个 `unwrap` 是一个**未声明的 panic 入口**。在 web handler 里 panic 会让这个请求(在 tokio 多线程下未必整个 server)挂掉,而 `Option`/`Result` 本来就是在逼你处理"可能没有/可能失败"——`unwrap` 等于把类型系统给的安全网剪了。

**better**:
- 可恢复失败 → 用 `?` 向上传播,配合 `thiserror`(库)或 `anyhow`(应用)。见 `error-handling.md`。
- 缺省值 → `unwrap_or` / `unwrap_or_else` / `unwrap_or_default`。
- `Mutex` 中毒 → 业务层一般 `if let Ok(g) = m.lock()` 或用不会中毒的 `parking_lot::Mutex`(用前 Context7 校验)。
- **可以** `expect` 的地方:常量正则/编译期已知不变量,且 message 写明*为什么*不可能失败(`expect("regex is a valid literal")`)——这是文档化的不变量,不是偷懒。测试、原型、`main` 顶层同样宽松。

```rust
// 生产 handler 里:别 unwrap
let id: i64 = req.param("id").ok_or(MyError::BadId)?;     // 传播,不 panic
let cfg = std::env::var("PORT").unwrap_or_else(|_| "8080".into());  // 有缺省
```

## 静默吞错误(`let _ = ...`)

**症状**: `let _ = write_all(buf);`、`let _ = tx.send(msg);`、`let _ = fs::remove_file(p);` —— 用 `let _ =` 把 `#[must_use]` 的 `Result` 直接丢掉。

**为何坏**: `let _ =` **立即丢弃**返回值并跳过 must_use lint,于是 I/O 失败、channel 已关闭、写入只写了一半全部无声蒸发。这类 bug 不会在出错时报出来,而是在很久以后以"数据怎么少了一截 / 消息没收到"的形式出现,根因极难回溯。(注意 `let _ = guard;` 是另一回事——那会*立刻*drop,常见于误删了想保留的锁/守卫,见下。)

**better**: 至少做一件事:传播、记录、或显式注释为什么真的可以忽略。

```rust
write_all(buf)?;                                   // 传播
if let Err(e) = tx.send(msg) { tracing::warn!(?e, "receiver gone"); }  // 记录
let _ = fs::remove_file(&tmp);                     // 仅当真的无所谓: 注明 "best-effort cleanup, ok if absent"
```

陷阱:`let _ = lock.lock();` 会**立即释放锁**(临时值即刻 drop),几乎一定是 bug——你想要的是 `let _guard = lock.lock()?;`(具名绑定,持有到作用域结束)。`let _` 与 `let _name` 的 drop 时机不同,这是高频坑。

## `Box<dyn Error>` 丢掉类型信息

**症状**: 库 / 业务模块里所有函数都返回 `Result<T, Box<dyn std::error::Error>>`(或 `Box<dyn Error + Send + Sync>`)。

**为何坏**: 调用方拿到的是一个**擦除了类型的错误**,无法 `match` 出"是哪种失败",只能 `to_string()` 看文案或 `downcast` 猜类型。对**库**尤其有害:你把"让调用方据错误类型分支处理"的能力剥夺了,等于公开 API 缺了一块。

**better**:
- **库** → 定义自己的 enum 错误类型,用 `thiserror` 派生 `Error`/`Display`/`From`(2.x API 用前 Context7 校验)。调用方能 `match` 各变体。
- **应用 / 二进制顶层** → `anyhow::Result<T>` 是合理的,因为顶层通常只想"打印 + 退出",不再分支;且 `anyhow` 还带 backtrace 和 `.context()`,比裸 `Box<dyn Error>` 信息更多。
- 判据:**这个错误的消费者需不需要按类型分情况处理?** 需要 → 具名 enum;只需汇报 → anyhow。

```rust
// 库: 保留类型, 调用方可 match
#[derive(thiserror::Error, Debug)]
pub enum StoreError {
    #[error("key not found: {0}")]
    NotFound(String),
    #[error("backend io")]
    Io(#[from] std::io::Error),
}
pub fn get(k: &str) -> Result<Vec<u8>, StoreError> { /* ... */ }
```

完整的 thiserror vs anyhow 选型与 `?`+`From` 见 `error-handling.md`。

## async 里阻塞 runtime

**症状**: 在 `async fn` / tokio 任务里调用 `std::fs::read`、`std::thread::sleep`、`reqwest::blocking`、重 CPU 循环,或 `block_on`。

**为何坏**: tokio 的 worker 线程少(默认 = 核数)。一个 `.await` 点之间的代码是**协作式、不可抢占**地占着 worker 线程——一旦在里面同步阻塞,这个 worker 就不能 poll 其它任务,**整个 runtime 的吞吐被一个阻塞调用拖死**,表现为"明明并发量不高却全卡住"。`block_on` 在已有 runtime 内还会直接 panic。

**better**:
- 有 async 版就用 async 版:`tokio::fs`、`tokio::time::sleep`、`reqwest`(非 blocking)。
- 没有 async 版的阻塞 I/O / 第三方同步库 → `tokio::task::spawn_blocking(|| ...)`,挪到专用阻塞线程池。
- 重 CPU 计算 → `rayon` 或 `spawn_blocking`,别在 async 任务里直接跑。
- 持有 `!Send` 的东西(`std::sync::MutexGuard`、`Rc`)**跨 `.await`** 会让 future 变 `!Send`,无法 `tokio::spawn` → 在 `.await` 前 drop 掉,或换 `tokio::sync::Mutex`。

```rust
// 阻塞 worker
let data = std::fs::read("big.bin")?;          // 卡住整个 runtime
// async I/O
let data = tokio::fs::read("big.bin").await?;
// 无 async 版的同步活
let out = tokio::task::spawn_blocking(move || heavy_sync(input)).await?;
```

`Send`-across-`.await` 的完整推理、`spawn` vs `spawn_blocking` vs `rayon` 见 `async-tokio.md`。

## `Arc<Mutex<T>>` 滥用

**症状**: 任何要在任务/线程间共享的状态,无脑包成 `Arc<Mutex<T>>`;甚至只读数据也这么包。

**为何坏**: `Mutex` 是**串行化点**——所有访问排队过锁,把潜在的并发又压回顺序执行,高竞争下吞吐塌方,还容易锁顺序死锁。很多场景根本不需要互斥,套上 `Arc<Mutex>` 是"我知道要共享,但没分析清楚共享的是什么"的默认动作。

**better**: 按"共享的是什么"选:

| 需求 | 用 |
|---|---|
| 只读共享(配置、查表) | `Arc<T>`,无锁 |
| 计数器 / 标志位 | `Arc<AtomicU64>` / `AtomicBool`,无锁原子 |
| 任务间传消息(而非共享内存) | channel:`tokio::sync::mpsc` / `watch` / `broadcast`。"share memory by communicating" |
| 读多写少 | `Arc<RwLock<T>>`(注意写饥饿) |
| 跨 `.await` 持有锁 | **`tokio::sync::Mutex`**,不是 `std::sync::Mutex`(后者的 guard `!Send`,跨 await 会污染 future) |
| 真的需要短暂临界区互斥 | `Arc<Mutex<T>>` 是对的——锁的范围尽量小,别跨 `.await` |

```rust
// 只读却上了锁
let cfg = Arc::new(Mutex::new(load_config()));   // 每次读都排队
// 只读 → 直接共享, 零竞争
let cfg = Arc::new(load_config());
```

`Send`/`Sync` 的 trait 推理见 `traits-generics.md`;tokio vs std 锁的选择见 `async-tokio.md`。

---

## 零成本抽象 / 性能小贴士

不与 profiler 重叠——这些是**写代码时就该有的默认习惯**,不是"事后拿 profiler 找热点"。具体某段是否真是瓶颈,交给 `cargo flamegraph` / `criterion` 实测(profiler 的活,本 skill 不替代)。

- **`Vec::with_capacity(n)` / `String::with_capacity`**:已知大致长度就预分配,省掉多次 `realloc` + 元素搬迁。`collect` 自带 size hint,但手动 `push` 的循环最容易忘。`HashMap::with_capacity` 同理。
- **借用而非克隆**:函数参数优先 `&str`/`&[T]` 而非 `String`/`Vec<T>`;迭代用 `iter()`/`&v` 而非 `into_iter()`(除非真要消费)。返回"借或拥有看情况"用 `Cow<'_, str>`。
- **迭代器是零成本的**:`iter().filter().map().sum()` 编译后与手写循环等价(LLVM 把适配器内联掉),别为"性能"把它改回 index 循环——可读性更差且不会更快。但**避免无谓的中间 `collect`**:`a.iter().chain(b.iter())` 比 `let mut v = a.clone(); v.extend(b);` 好。
- **单态化(monomorphization)膨胀**:泛型函数会为每个具体类型各生成一份机器码,深度泛型 + 多类型实例化会撑大二进制、拖慢编译。对**大体积、非热点**的泛型函数,可在内部把活转发给一个 `dyn Trait` / 非泛型 inner fn(thin generic wrapper + fat dyn core),用一点动态分发换代码体积。热点路径则保持泛型静态分发。
- **`#[inline]` 别乱撒**:跨 crate 的小函数标 `#[inline]` 有意义;同 crate 内编译器自会判断。无脑全标只会拖慢编译、撑大二进制。
- **`format!` 在热路径**:`format!` 每次分配新 `String`。循环里拼字符串用 `write!(&mut buf, ...)` 复用 buffer,或预分配后 `push_str`。
