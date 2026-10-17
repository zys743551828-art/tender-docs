import { useState, type RefObject } from "react";
import { useAssistantContinuation } from "./useAssistantContinuation";
import { api, ApiError } from "./api";
import type {
  AssistantAnswerPart,
  AssistantGlobalConversation,
  AssistantJob,
  AssistantJobPage,
  AssistantTurnRequest,
  Bid,
  WorkConversationView,
  WorkMessageRequest,
  WorkMessageView,
} from "./contracts";
import type {
  AssistantConversation,
  AssistantMessage,
  SelectedAssistantSource,
} from "./assistantState";
import {
  assistantReason,
  assistantStates,
  sourceKinds,
  isSelectedAssistantSource,
} from "./assistantState";
import { AssistantSourcePicker } from "./AssistantSourcePicker";
import { AssistantActionCard, AssistantJobCard } from "./AssistantActionCard";
import { Badge, ErrorNotice } from "./components";
import { ArtifactActions } from "./ArtifactActions";
import { EvidenceLinks } from "./EvidenceLinks";
import { knowledgeStorageKey, useKnowledgeInput } from "./knowledgeStorage";
import { ResourceState } from "./ResourceState";
import { useResource } from "./useResource";
import { useRecordedCommand } from "./useRecordedCommand";
import {
  useAssistantGlobalEvents,
  useAssistantMessages,
  useAssistantPolling,
} from "./useAssistantFeed";
import { useProjectStream } from "./useProjectStream";
import { useBidChoices } from "./useBidChoices";

export function AssistantHub({
  memberId,
  bidId,
}: {
  memberId: string;
  bidId: string | null;
}) {
  const bids = useBidChoices();
  return (
    <div className="page-content assistant-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">有据讨论 · 操作明确</span>
          <h1>工作助手</h1>
          <p className="muted">
            查找与讨论保留在同一主会话，正式判断仍走业务工作面。
          </p>
        </div>
      </header>
      <section className="panel assistant-scope">
        <label>
          会话范围
          <select
            aria-label="助手会话范围"
            value={bidId ?? ""}
            onChange={(event) => {
              window.location.href = event.target.value
                ? `/assistant?bid=${encodeURIComponent(event.target.value)}`
                : "/assistant";
            }}
          >
            <option value="">全局检索 · 我的私有主会话</option>
            {bids.items.map((bid) => (
              <option value={bid.id} key={bid.id}>
                {bid.title} · 项目共享主会话
              </option>
            ))}
            {bidId && !bids.items.some((bid) => bid.id === bidId) && (
              <option value={bidId}>明确指定的项目主会话</option>
            )}
          </select>
        </label>
        <ResourceState
          {...bids}
          hasData={!!bids.data}
          onRetry={() => void bids.reload()}
        />
        <ErrorNotice error={bids.moreError} />
        {bids.hasMore && (
          <button
            className="text-button"
            disabled={bids.moreBusy}
            onClick={() => void bids.loadMore()}
          >
            加载更多会话项目
          </button>
        )}
      </section>
      {new URLSearchParams(window.location.search).get("action") && (
        <section className="panel">
          <h2>明确链接的操作提议</h2>
          <AssistantActionCard
            id={new URLSearchParams(window.location.search).get("action")!}
            memberId={memberId}
          />
        </section>
      )}
      <AssistantWorkspace
        key={`${memberId}:${bidId ?? "global"}`}
        memberId={memberId}
        bidId={bidId}
      />
    </div>
  );
}

export function AssistantWorkspace({
  memberId,
  bidId,
  embedded = false,
}: {
  memberId: string;
  bidId: string | null;
  embedded?: boolean;
}) {
  const interval = useAssistantPolling();
  const mainPath = bidId
    ? `/bids/${bidId}/conversations/main`
    : "/assistant/global-conversations/main";
  const resource = useResource<AssistantConversation>(mainPath, interval);
  const project = useResource<Bid>(bidId ? `/bids/${bidId}` : null);
  useProjectStream(
    !embedded && project.data?.id === bidId
      ? project.data.stream_id
      : undefined,
    bidId ?? "",
  );
  const conversation =
    resource.data?.bid_id === bidId &&
    resource.data.scope === (bidId ? "project" : "global") &&
    resource.data.is_main
      ? resource.data
      : undefined;
  const missing =
    resource.error instanceof ApiError && resource.error.status === 404;
  const ensure = useRecordedCommand(
    `assistant-main:${memberId}:${bidId ?? "global"}`,
  );
  const state = useRecordedCommand(
    `assistant-state:${memberId}:${bidId ?? "global"}`,
  );
  const eventsError = useAssistantGlobalEvents(
    conversation?.id,
    memberId,
    !bidId,
  );
  const [showState, setShowState] = useState(false);
  const path = conversation
    ? bidId
      ? `/bids/${bidId}/conversations/${conversation.id}`
      : `/assistant/global-conversations/${conversation.id}`
    : null;
  function refresh() {
    void resource.reload();
    window.dispatchEvent(new Event("server-snapshot-change"));
  }
  return (
    <div className="assistant-workspace">
      <section className="panel assistant-context">
        <div className="section-title">
          <div>
            <h2>
              {bidId
                ? project.data?.id === bidId
                  ? project.data.title
                  : "项目主会话"
                : "全局检索 · 私有主会话"}
            </h2>
            <p className="small-note">
              {bidId
                ? "本项目成员共享讨论。只读记忆和已保存来源不等于当前稿已核对。"
                : "这段讨论仅你可见。选择项目或资料作为上下文。"}
            </p>
          </div>
          {conversation && <Badge>{assistantStates[conversation.state]}</Badge>}
        </div>
        {bidId && (
          <ResourceState
            {...project}
            hasData={!!project.data}
            onRetry={() => void project.reload()}
          />
        )}
        {!missing && (
          <ResourceState
            {...resource}
            hasData={!!conversation}
            onRetry={() => void resource.reload()}
          />
        )}
        {resource.data && !conversation && (
          <p className="notice error">
            主会话身份或范围不一致，未显示其他讨论。
          </p>
        )}
        {missing && (
          <>
            <p>此范围尚无主会话记录。创建后，可在其他浏览器从服务器恢复。</p>
            <button
              className="button primary"
              disabled={resource.loading || ensure.busy || !!ensure.pending}
              onClick={() =>
                void ensure
                  .run<
                    WorkConversationView | AssistantGlobalConversation
                  >(mainPath, {}, "")
                  .then(refresh)
              }
            >
              创建并继续主会话
            </button>
          </>
        )}
        <ErrorNotice error={ensure.error} />
        {ensure.pending && !ensure.busy && (
          <button
            className="button secondary"
            onClick={() =>
              void ensure.retry<AssistantConversation>().then(refresh)
            }
          >
            恢复原主会话创建结果
          </button>
        )}
        {conversation && (
          <>
            <div className="review-shortcuts">
              <button
                className="text-button"
                onClick={() => setShowState(!showState)}
                aria-expanded={showState}
              >
                会话显示与结束
              </button>
              {embedded && (
                <a href={`/assistant?bid=${encodeURIComponent(bidId ?? "")}`}>
                  全屏打开同一主会话
                </a>
              )}
              <button className="text-button" onClick={refresh}>
                刷新会话与真实处理记录
              </button>
            </div>
            {showState && (
              <div className="assistant-state-controls">
                <p className="small-note">
                  结束或归档只改变会话展示，不取消任务、不关闭项目、不删除文件与决定。已归档会话先恢复为已结束，再明确继续。
                </p>
                <ErrorNotice error={state.error} />
                {(conversation.state === "hidden"
                  ? (["closed"] as const)
                  : conversation.state === "closed"
                    ? (["active", "hidden"] as const)
                    : (["closed", "hidden"] as const)
                ).map((target) => (
                  <button
                    key={target}
                    className="button secondary"
                    disabled={
                      resource.loading ||
                      !!resource.error ||
                      state.busy ||
                      !!state.pending ||
                      !resource.etag
                    }
                    onClick={() =>
                      void state
                        .run<AssistantConversation>(
                          `${path}/state`,
                          { state: target },
                          resource.etag ?? "",
                        )
                        .then(refresh)
                    }
                  >
                    {target === "active"
                      ? "继续这条会话"
                      : target === "hidden"
                        ? "归档这条会话"
                        : conversation.state === "hidden"
                          ? "恢复为已结束会话"
                          : "结束这条会话"}
                  </button>
                ))}
                {state.pending && !state.busy && (
                  <button
                    className="text-button"
                    onClick={() =>
                      void state.retry<AssistantConversation>().then(refresh)
                    }
                  >
                    恢复原会话状态操作
                  </button>
                )}
              </div>
            )}
          </>
        )}
        {!!eventsError && (
          <p className="notice warning">
            处理记录暂时未能同步，已读取内容仍保留，可刷新重新核对。
          </p>
        )}
      </section>
      {conversation && path && (
        <ConversationContent
          key={conversation.id}
          conversation={conversation}
          path={path}
          etag={resource.etag}
          memberId={memberId}
          onChanged={refresh}
        />
      )}
    </div>
  );
}

function ConversationContent({
  conversation,
  path,
  etag,
  memberId,
  onChanged,
}: {
  conversation: AssistantConversation;
  path: string;
  etag: string | null;
  memberId: string;
  onChanged: () => void;
}) {
  const interval = useAssistantPolling();
  const messages = useAssistantMessages(
    `${path}/messages?limit=50`,
    conversation.id,
    conversation.bid_id,
  );
  const params = new URLSearchParams({
    scope: conversation.scope,
    conversation_id: conversation.id,
    limit: "100",
  });
  if (conversation.bid_id) params.set("bid_id", conversation.bid_id);
  const jobs = useResource<AssistantJobPage>(
    `/assistant/jobs?${params}`,
    interval,
  );
  const validJobs =
    jobs.data?.items.filter(
      (job) =>
        job.conversation_id === conversation.id &&
        job.bid_id === conversation.bid_id &&
        job.scope === conversation.scope,
    ) ?? [];
  const foreign = !!jobs.data && validJobs.length !== jobs.data.items.length;
  const ongoing = validJobs.filter(
    (job) =>
      job.requires_billing_reconciliation ||
      ["queued", "running", "cancel_requested", "failed"].includes(
        job.operation.state,
      ) ||
      job.publication?.state === "stale" ||
      job.publication?.state === "failed",
  );
  const ended = validJobs.filter(
    (job) => !ongoing.some((item) => item.id === job.id),
  );
  const blocked =
    foreign ||
    !!jobs.error ||
    !jobs.data ||
    validJobs.some(
      (job) =>
        job.requires_billing_reconciliation ||
        ["queued", "running", "cancel_requested"].includes(job.operation.state),
    );
  const continuation = useAssistantContinuation(
    (!!messages.data || !!messages.error) && !messages.loading && !jobs.loading,
    !new URLSearchParams(window.location.search).has("action"),
  );
  return (
    <div className="assistant-conversation" ref={continuation.content}>
      <nav className="assistant-continue-bar" aria-label="会话快速定位">
        <button
          className="button secondary"
          onClick={continuation.continueWriting}
        >
          回到最新／继续提问
        </button>
      </nav>
      <section className="panel assistant-messages" aria-label="持久会话消息">
        <h3>主会话记录</h3>
        <ResourceState
          {...messages}
          hasData={!!messages.data}
          onRetry={() => void messages.reload()}
        />
        <ErrorNotice error={messages.identityError ?? messages.moreError} />
        {messages.hasMore && (
          <button
            className="text-button"
            disabled={messages.moreBusy}
            onClick={() => void messages.more()}
          >
            加载更早消息
          </button>
        )}
        {messages.data && !messages.items.length && !messages.identityError && (
          <p className="small-empty">
            尚无已保存消息。下方草稿只有明确保存或受理后才进入服务器记录。
          </p>
        )}
        {messages.items.map((message) => (
          <AssistantMessageCard
            key={message.id}
            message={message}
            memberId={memberId}
          />
        ))}
      </section>
      <section className="assistant-jobs" aria-label="助手处理与费用状态">
        <ResourceState
          {...jobs}
          hasData={!!jobs.data}
          onRetry={() => void jobs.reload()}
        />
        {foreign && (
          <p className="notice error">
            处理列表包含其他会话范围，未用于当前操作。
          </p>
        )}
        {ongoing.map((job) => (
          <AssistantJobCard
            key={job.id}
            id={job.id}
            memberId={memberId}
            conversationId={conversation.id}
            bidId={conversation.bid_id}
          />
        ))}
        {ended.length > 0 && (
          <details className="coverage-details">
            <summary>已结束的助手处理 · {ended.length}项</summary>
            {ended.map((job) => (
              <AssistantJobCard
                key={job.id}
                id={job.id}
                memberId={memberId}
                conversationId={conversation.id}
                bidId={conversation.bid_id}
              />
            ))}
          </details>
        )}
        {validJobs.length > 0 && (
          <p className="small-note">
            服务器最近最多100项处理记录，不作为完整任务档案；历史消息按实际游标另行读取。
          </p>
        )}
      </section>
      <AssistantComposer
        inputRef={continuation.input}
        conversation={conversation}
        path={path}
        etag={etag}
        memberId={memberId}
        busyJob={blocked}
        onChanged={() => {
          onChanged();
          void messages.reload();
          void jobs.reload();
        }}
      />
    </div>
  );
}

function AssistantComposer({
  inputRef,
  conversation,
  path,
  etag,
  memberId,
  busyJob,
  onChanged,
}: {
  conversation: AssistantConversation;
  path: string;
  etag: string | null;
  memberId: string;
  busyJob: boolean;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onChanged: () => void;
}) {
  const key =
    knowledgeStorageKey(memberId, conversation.id) + "assistant-compose:";
  const [text, setText] = useKnowledgeInput(key + "text", "");
  const [cachedSources, setSources] = useKnowledgeInput<
    SelectedAssistantSource[]
  >(key + "sources", []);
  const sources = cachedSources
    .filter(isSelectedAssistantSource)
    .filter(
      (source) => !conversation.bid_id || source.bidId === conversation.bid_id,
    );
  const [initialId] = useState(() => crypto.randomUUID());
  const [clientId, setClientId] = useKnowledgeInput(
    key + "message-id",
    initialId,
  );
  const [basis, setBasis] = useKnowledgeInput(key + "etag", "");
  const [sectionId, setSectionId] = useKnowledgeInput(key + "section", "");
  const [showSources, setShowSources] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const turn = useRecordedCommand(
    `assistant-turn:${memberId}:${conversation.id}`,
  );
  const note = useRecordedCommand(
    `assistant-note:${memberId}:${conversation.id}`,
  );
  const locked = turn.busy || !!turn.pending || note.busy || !!note.pending;
  const active = conversation.state === "active";
  const accept = (value: AssistantJob | WorkMessageView | undefined) => {
    if (!value) return;
    if (
      value.conversation_id !== conversation.id ||
      value.bid_id !== conversation.bid_id
    ) {
      setFailure(new Error("写入结果的会话身份不一致，请刷新核对。"));
      return;
    }
    setText("");
    setSources([]);
    setSectionId("");
    setBasis("");
    setClientId(crypto.randomUUID());
    onChanged();
  };
  function capture() {
    if (!basis && etag) setBasis(etag);
  }
  async function send(asNote: boolean) {
    if (
      !text.trim() ||
      !basis ||
      !active ||
      locked ||
      turn.conflict ||
      note.conflict ||
      (!asNote && busyJob)
    )
      return;
    setFailure(null);
    if (asNote) {
      const request: WorkMessageRequest = {
        client_message_id: clientId,
        text: text.trim(),
        source_version_ids: sources.map((source) => source.reference.id),
      };
      const value = await note.run<WorkMessageView>(
        `${path}/messages`,
        request,
        basis,
      );
      accept(value?.data);
    } else {
      const request: AssistantTurnRequest = {
        client_message_id: clientId,
        text: text.trim(),
        source_selection: sources.map((source) => source.reference),
        section_id: sources.some(
          (source) =>
            source.reference.kind === "section" &&
            source.reference.id === sectionId,
        )
          ? sectionId
          : null,
      };
      const value = await turn.run<AssistantJob>(
        `${path}/turns`,
        request,
        basis,
      );
      accept(value?.data);
    }
  }
  async function rebase() {
    setFailure(null);
    try {
      const current = await api<AssistantConversation>(
        conversation.scope === "global"
          ? "/assistant/global-conversations/main"
          : path,
      );
      if (
        current.data.id !== conversation.id ||
        current.data.bid_id !== conversation.bid_id ||
        !current.etag
      )
        throw new Error("会话基准身份不一致，输入保留。");
      setBasis(current.etag);
      turn.clearError();
      note.clearError();
      onChanged();
    } catch (error) {
      setFailure(error);
    }
  }
  return (
    <section className="panel assistant-composer">
      <h3>继续上次工作</h3>
      <p className="small-note">
        草稿按成员与此主会话在浏览器保留。明确发给助手才请求处理；聊天和参考不是正式评分输入。
      </p>
      {!active && (
        <p className="notice warning">
          此会话{assistantStates[conversation.state]}
          ，草稿保留。请从会话状态明确继续后发送。
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void send(false);
        }}
      >
        <label>
          本次提问
          <textarea
            ref={inputRef}
            rows={4}
            aria-label="本次提问"
            maxLength={20000}
            value={text}
            disabled={locked}
            onChange={(event) => {
              capture();
              setText(event.target.value);
            }}
            onKeyUp={(event) => {
              if (event.key === "@") setShowSources(true);
            }}
            placeholder="例如：结合我选的资料，解释需要关注的范围。使用 @ 或下方按钮选择真实来源。"
          />
        </label>
        <div className="assistant-selected-sources" aria-label="本消息已选来源">
          {sources.map((source, index) => (
            <div
              key={`${source.reference.kind}:${source.reference.id}:${index}`}
            >
              <span>
                {sourceKinds[source.reference.kind]} · {source.title}
                {source.reference.physical_page
                  ? ` · 物理页${source.reference.physical_page}`
                  : ""}
              </span>
              <button
                type="button"
                className="text-button"
                disabled={locked}
                aria-label={`移除本次来源：${source.title}`}
                onClick={() =>
                  setSources((old) => old.filter((_, i) => i !== index))
                }
              >
                移除
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          className="text-button"
          disabled={locked}
          aria-expanded={showSources}
          onClick={() => setShowSources(!showSources)}
        >
          ＋ / @ 选择可信来源
        </button>
        {showSources && (
          <AssistantSourcePicker
            bidId={conversation.bid_id}
            selected={sources}
            onChange={(value) => {
              capture();
              setSources(value);
            }}
            disabled={locked}
            traceId={new URLSearchParams(window.location.search).get("trace")}
          />
        )}
        {sources.some((source) => source.reference.kind === "section") && (
          <label>
            本次明确的章节上下文
            <select
              aria-label="本次章节上下文"
              disabled={locked}
              value={sectionId}
              onChange={(event) => setSectionId(event.target.value)}
            >
              <option value="">仅作项目讨论，不指定修改章节</option>
              {sources
                .filter((source) => source.reference.kind === "section")
                .map((source) => (
                  <option key={source.reference.id} value={source.reference.id}>
                    {source.title}
                  </option>
                ))}
            </select>
          </label>
        )}
        <ErrorNotice error={failure ?? turn.error ?? note.error} />
        {(turn.conflict || note.conflict) && (
          <p className="notice warning">
            会话或来源已变化，输入保留。请刷新消息与可信来源后明确核对基准；旧来源不会自动换成当前版。
          </p>
        )}
        <div className="review-shortcuts">
          <button
            type="button"
            className="text-button"
            disabled={locked || !etag}
            onClick={() => void rebase()}
          >
            已核对会话，按当前修订继续
          </button>
          <button
            className="button primary"
            disabled={
              locked ||
              !active ||
              busyJob ||
              !text.trim() ||
              !basis ||
              !!turn.conflict ||
              !!note.conflict
            }
          >
            {turn.busy ? "正在取得受理结果…" : "发送给助手"}
          </button>
          {conversation.bid_id && (
            <button
              type="button"
              className="button secondary"
              disabled={
                locked ||
                !active ||
                !text.trim() ||
                !basis ||
                sources.some((source) => source.reference.kind !== "document")
              }
              onClick={() => void send(true)}
            >
              仅保存讨论，不请求AI
            </button>
          )}
        </div>
        {busyJob && (
          <p className="small-note">
            助手处理尚在途、费用待核对或处理列表尚未读全，本次不会重复启动。草稿仍可编辑。
          </p>
        )}
        {conversation.bid_id && sources.length > 0 && (
          <p className="small-note">
            仅保存讨论只能附带原件版本，不保存章节、知识或物理页选区；这些来源可留在草稿或明确发给助手。
          </p>
        )}
      </form>
      {turn.pending && !turn.busy && (
        <div className="notice warning">
          <p>
            原提问的受理答复尚未取得，先恢复同一请求，不换消息标识或付费尝试。
          </p>
          <button
            className="button secondary"
            onClick={() =>
              void turn
                .retry<AssistantJob>()
                .then((value) => accept(value?.data))
            }
          >
            恢复原提问受理结果
          </button>
        </div>
      )}
      {note.pending && !note.busy && (
        <button
          className="button secondary"
          onClick={() =>
            void note
              .retry<WorkMessageView>()
              .then((value) => accept(value?.data))
          }
        >
          恢复原讨论保存结果
        </button>
      )}
    </section>
  );
}

function AssistantMessageCard({
  message,
  memberId,
}: {
  message: AssistantMessage;
  memberId: string;
}) {
  return (
    <article className={`assistant-message role-${message.role}`}>
      <div className="assistant-message-heading">
        <strong>
          {message.role === "user"
            ? "成员消息"
            : message.role === "assistant"
              ? "助手参考答复"
              : "处理记录"}
        </strong>
        <small>{new Date(message.created_at).toLocaleString("zh-CN")}</small>
      </div>
      {message.parts.map((part, index) =>
        part.type === "text" ? (
          <p className="preserve-text" key={index}>
            {part.text}
          </p>
        ) : part.type === "source_reference" ? (
          <ArtifactActions
            key={index}
            bidId={part.source_bid_id}
            documentId={part.document_version_id}
            isPdf
            downloadLabel="核对当时附带的资料原件"
          />
        ) : (
          <AssistantAnswer key={index} part={part} memberId={memberId} />
        ),
      )}
    </article>
  );
}
function AssistantAnswer({
  part,
  memberId,
}: {
  part: AssistantAnswerPart;
  memberId: string;
}) {
  return (
    <div className="assistant-answer">
      <p className="small-note">
        助手参考；问题建议不是正式问题记录，采纳不等于解决。
      </p>
      {part.gaps.map((gap, index) => (
        <p className="notice warning" key={index}>
          {gap}
        </p>
      ))}
      {part.citations.map((citation, index) => (
        <section className="assistant-citation" key={index}>
          <blockquote>{citation.quote}</blockquote>
          <Badge>
            {citation.position.verbatim_original
              ? "原文引用"
              : "派生参考，非逐字原文"}
          </Badge>
          {citation.position.kind === "knowledge_retrieval_unit" &&
            citation.position.knowledge_provenance?.kind ===
              "derived_knowledge" && (
              <p className="small-note">
                此为知识整理内容，批准原件仅是其依据，不把整理文字冒充原页原文。
              </p>
            )}
          <EvidenceLinks
            ids={
              citation.position.evidence_ids.length
                ? citation.position.evidence_ids
                : citation.position.knowledge_provenance?.kind ===
                    "derived_knowledge"
                  ? citation.position.knowledge_provenance.approved_references.map(
                      (ref) => ref.evidence_id,
                    )
                  : citation.position.knowledge_provenance?.kind ===
                      "original_quote"
                    ? [
                        citation.position.knowledge_provenance.reference
                          .evidence_id,
                      ]
                    : []
            }
            label="核对答复实际来源原页"
          />
          {citation.position.document_version_id &&
            !citation.position.evidence_ids.length && (
              <ArtifactActions
                bidId={citation.position.bid_id}
                documentId={citation.position.document_version_id}
                isPdf
                downloadLabel="核对答复对应的原件版本"
              />
            )}
        </section>
      ))}
      {part.issue_suggestions.map((item) => (
        <div className="assistant-suggestion" key={item.suggestion_id}>
          <Badge tone="warning">助手建议 · 尚非正式风险</Badge>
          <p>{item.statement}</p>
          <p className="small-note">
            {item.needs_basis
              ? "仍需补足依据；没有因此新建事实或解决问题。"
              : "供进一步核对；没有因此新建事实或解决问题。"}
          </p>
        </div>
      ))}
      {part.rejected_proposals.map((proposal, index) => (
        <p className="notice warning" key={index}>
          此提议未形成可执行卡：{assistantReason(proposal.error_code)}
        </p>
      ))}
      {part.action_ids.map((id) => (
        <AssistantActionCard key={id} id={id} memberId={memberId} />
      ))}
    </div>
  );
}
