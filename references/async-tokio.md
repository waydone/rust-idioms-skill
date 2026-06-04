# Async & Tokio

异步是 Rust 里"能编译 ≠ 惯用"落差最大的区域。本文聚焦三类高频根因:**future 不是 `Send`**、**`!Send` 跨 `.await`**、**该用哪个并发原语 / 哪种 spawn**。语法(`async fn` 返回 `Future`、`.await` poll)假设已会,不复述。

Tokio API 非平凡处(`select!` 公平性、`JoinSet`、`spawn_blocking` 池语义、`CancellationToken`)**用前 Context7 校验** `tokio` 1.x —— 训练数据落后于当前 release。

## 第一性问题:I/O-bound 还是 CPU-bound

选错执行模型是最贵的错。先判定:

| 工作性质 | 判据 | 正确归宿 |
|---|---|---|
| I/O-bound | 大部分时间在等待(网络 / 磁盘 / DB / 子进程) | `async` + `.await`,跑在 tokio runtime 上 |
| CPU-bound,短(< 几十 µs) | 纯计算,不阻塞,很快返回 | 直接在 async 里同步算,不必 spawn |
| CPU-bound,长 | 图像 / 加密 / 压缩 / 大循环,会霸占线程 | `spawn_blocking`(偶尔)或 `rayon`(数据并行) |
| 阻塞型 syscall / 同步库 | `std::fs`、`reqwest::blocking`、阻塞的 C FFI、`Mutex` 长持有 | `spawn_blocking` |

**根因认知**:tokio 默认是 work-stealing 多线程 runtime,但**每个 worker 线程靠任务主动 `.await` 让出**。一个 CPU-bound 的长循环或一次阻塞 syscall **不会让出**,它霸占整个 worker 线程 —— 其它任务饿死,延迟尖刺。这不是报错,是性能塌方,比编译错误更隐蔽。

## "future is not `Send`" / `spawn` 的约束

`tokio::spawn` 要求 future 是 `Send + 'static`,因为任务可能被 work-stealing 调度到**另一个**线程。报错通常长这样:

```text
error: future cannot be sent between threads safely
  note: future is not `Send` as this value is used across an await
  note: `std::rc::Rc<...>` ... has type `Rc<...>` which is not `Send`
```

判定规则:**一个 future 是 `Send`,当且仅当所有"跨 `.await` 存活"的局部变量都是 `Send`**。`.await` 之间用完即丢的 `!Send` 值不影响。

### 跨 `.await` 持有 `!Send` 的两大元凶

**(1) `MutexGuard`(`std::sync::Mutex`)跨 `.await`** —— 最常见,且同时是性能/死锁炸弹:

```rust
// 反模式:guard 活过 .await → future !Send,且阻塞期间锁被一直握着
async fn bad(state: &std::sync::Mutex<Vec<u8>>) {
    let mut g = state.lock().unwrap();
    g.push(0);
    do_io().await;          // ← guard 仍在作用域,跨越了 await
    g.push(1);
}
```

两条正交的解法,按需求选:

```rust
// 解法 A:缩短 guard 生命周期到 await 之前(锁不跨 await)——首选
async fn fix_a(state: &std::sync::Mutex<Vec<u8>>) {
    {
        let mut g = state.lock().unwrap();
        g.push(0);
    }                       // ← guard 在此 drop
    do_io().await;
}

// 解法 B:真的需要"持锁跨 await"才用 tokio::sync::Mutex(它的 guard 是 Send)
async fn fix_b(state: &tokio::sync::Mutex<Vec<u8>>) {
    let mut g = state.lock().await;   // .await 拿锁
    g.push(0);
    do_io().await;          // 合法,但要清楚:锁全程被持有
    g.push(1);
}
# async fn do_io() {}
```

**不要**条件反射地把所有 `std::sync::Mutex` 换成 `tokio::sync::Mutex`。后者更慢、且"持锁跨 await"本身常是设计问题(锁内做 I/O = 串行化)。**优先解法 A**;只有逻辑上必须持锁跨越 await 点时才用 B。

**(2) `Rc` / `RefCell` 跨 `.await`** —— 它们 `!Send`。修法:换 `Arc`(共享所有权)、`Arc<Mutex<_>>`(共享可变)、或重构成不跨 await 持有。

### 何时 `spawn_local` 而非换类型

如果任务**本来就不想跨线程**(单线程逻辑、大量 `Rc`、`!Send` 的第三方库),用 `tokio::task::spawn_local` + `LocalSet`,跑在当前线程,**不要求 `Send`**。代价:失去 work-stealing,这条任务不并行扩展。这是"逃生舱",不是默认选择 —— 默认仍是让 future `Send`。

## `tokio::spawn` vs `spawn_blocking` vs `rayon`

| 机制 | 用于 | 关键点 |
|---|---|---|
| `tokio::spawn` | I/O-bound async 任务,需并发 | future 必须 `Send + 'static`;返回 `JoinHandle`;**任务 detach 后 panic 不会传播到 main**,要 `.await` handle 才看得到 `Err(JoinError)` |
| `spawn_blocking` | 偶发的阻塞调用 / 短 CPU 活,数量有限 | 跑在独立的 blocking 线程池(默认上限 512),**不适合大量长 CPU 任务**——会把池打满;返回的闭包是 `FnOnce + Send + 'static` |
| `rayon` | 数据并行的重 CPU 计算(`par_iter`) | 独立的 CPU 线程池,按核心数;**不要在 rayon 闭包里 `.await`**;async ↔ rayon 之间用 oneshot channel 桥接 |

**反模式**:在 async 里直接跑长 CPU 循环(卡 worker);或用 `spawn_blocking` 跑成百上千个并行计算(打满 blocking 池,且它不做 CPU 亲和调度)。重计算 → `rayon`;桥接示例:

```rust
async fn heavy(data: Vec<u8>) -> u64 {
    let (tx, rx) = tokio::sync::oneshot::channel();
    rayon::spawn(move || {
        let sum = data.iter().map(|&b| b as u64).sum();  // 真·CPU 活,在 rayon 池
        let _ = tx.send(sum);
    });
    rx.await.expect("rayon task dropped")
}
```

## 共享状态选型:`Arc<Mutex>` / `RwLock` / channel / Atomic

不要默认 `Arc<Mutex<_>>` 包一切。按访问模式选:

| 场景 | 选型 | 说明 |
|---|---|---|
| 单个标量计数 / 标志 | `Atomic*`(`AtomicU64`/`AtomicBool`) | 无锁,最快;注意选对 `Ordering`(多数 `Relaxed`,同步配对用 `Acquire`/`Release`) |
| 共享可变,锁**不**跨 `.await` | `Arc<std::sync::Mutex<T>>` | 默认首选;比 tokio 锁快;guard 不可跨 await |
| 读多写少,不跨 await | `Arc<std::sync::RwLock<T>>` | 多读并发;写饥饿要留意 |
| 必须**持锁跨 `.await`** | `Arc<tokio::sync::Mutex<T>>` | guard `Send`;但常意味着该重构(见上) |
| 任务间传递所有权 / 解耦生产消费 | channel(`mpsc`/`oneshot`/`broadcast`/`watch`) | "不要共享内存来通信,要通信来共享内存";避免共享锁 |
| 只读共享配置 | `Arc<T>`(无锁) | 不可变就别上锁 |

channel 选型速记:`oneshot` 单值回传(如 rayon 桥接、请求-响应);`mpsc` 多生产单消费(任务队列);`broadcast` 多消费、每个都收到副本;`watch` 单值最新状态广播(配置热更新)。具体容量 / `try_send` / 关闭语义**用前 Context7 校验** `tokio::sync`。

## `tokio::time::sleep` vs `thread::sleep`

```rust
tokio::time::sleep(std::time::Duration::from_secs(1)).await;  // 让出 worker,其它任务继续跑
std::thread::sleep(std::time::Duration::from_secs(1));        // 阻塞整个 worker 线程!async 里禁用
```

**红线**:async 上下文里出现 `std::thread::sleep`(或任何阻塞调用)= bug,会卡住该 worker 上的所有任务。需要阻塞睡眠的同步代码,放进 `spawn_blocking`。

## 取消、超时、`select!`

Rust future 的取消是**协作式**:drop 一个 future 就取消它 —— 它停在最近一个 `.await` 处,**不再被 poll**。这带来一条核心纪律:

```rust
use tokio::time::{timeout, Duration};

// 超时 = 包一层 timeout future,到点后内层 future 被 drop(取消)
match timeout(Duration::from_secs(5), do_io()).await {
    Ok(v)  => { /* 完成 */ }
    Err(_) => { /* 超时,do_io 已被取消 */ }
}
# async fn do_io() {}
```

```rust
// select!:同时跑多个分支,第一个完成的胜出,其余被 drop(取消)
tokio::select! {
    v = do_io()                       => { /* io 先完成 */ }
    _ = tokio::time::sleep(Duration::from_secs(5)) => { /* 超时分支 */ }
}
# async fn do_io() {}
```

**取消安全(cancel safety)是 `select!` 的头号陷阱**:被 drop 的分支若已部分推进(如已从 socket 读走半条消息存在局部变量里),那部分状态**丢失**。只有标注"cancel safe"的 API(如 `mpsc::Recv`)能在 `select!` 里安全反复使用;否则把进行中的状态存在循环外、或用 `tokio_util::sync::CancellationToken` 做显式协作取消。`select!` 的随机分支选择、`biased;` 模式、循环里的用法**用前 Context7 校验** `tokio::select`。

并发等多个 future:用 `JoinSet`(动态、可取消、收集结果)或 `futures::future::join_all`(固定集合)。`JoinSet` 的 `spawn`/`join_next`/`abort_all` 语义**用前 Context7 校验**。

## 结合你的栈:Salvo handler 的 async 约束

Salvo 跑在 tokio 上,handler 的 future 最终被 `tokio::spawn` 调度,所以**上面所有 `Send` 规则在 handler 里同样成立**。两条落地纪律:

**(1) handler 体内别跨 `.await` 持 `!Send` 值。** 典型踩坑:从 `Depot` 取出共享状态、`lock()` 后又 `.await`:

```rust
# use std::sync::{Arc, Mutex};
# struct State { hits: u64 }
// 反模式:std MutexGuard 跨 await → handler future !Send,编译报错
// let mut g = state.lock().unwrap();
// let body = fetch().await;      // ← guard 跨 await
// g.hits += 1;

// 修法:锁内只做同步、即刻 drop;I/O 在锁外
async fn ok(state: &Arc<Mutex<State>>) {
    let body = fetch().await;          // 先 I/O
    { state.lock().unwrap().hits += 1; }   // 再短临界区
    let _ = body;
}
# async fn fetch() -> String { String::new() }
```

**(2) 共享状态用 `Arc` 注入,满足 `Send + Sync + 'static`。** Salvo 的 state 注入(`affix_state` / `Depot`)要求状态 `Send + Sync + 'static` —— 因为它跨请求、跨线程共享。不可变配置直接 `Arc<Config>`;需要可变就 `Arc<Mutex<_>>`(锁不跨 await)或 `Arc<DashMap>` / channel。**纯 Salvo 的注入 API(`affix_state::inject`、`Depot::obtain`)细节交给 salvo-skill**;本文只负责"为什么是这些 bound"这一语言层根因:`'static` 因为生命周期不能绑定到单次请求,`Send + Sync` 因为多线程 runtime 并发触达同一份状态。

## 反模式速查

- `block_on` 套在 async 函数内部 / handler 内部 —— 会 panic 或死锁(runtime 套 runtime)。需要在同步代码里进同步世界才用 `block_on`,且在 runtime 之外。
- 用 `.clone()` + `tokio::spawn` 复制一堆 `Arc` 只为绕过借用 —— 先想是否该用 channel 传所有权。
- 给每个小结构都套 `Arc<Mutex<_>>` —— 多数共享是只读的,`Arc<T>` 足够;真要可变先看 Atomic / channel。
- `spawn` 后不持有 `JoinHandle` 也不管结果 —— 任务 panic 静默丢失;需要错误传播就 `.await` handle 或用 `JoinSet`。
- 在 `spawn_blocking` 里 `.await` tokio future —— 那是非 async 线程,没有 runtime 上下文,会 panic。
