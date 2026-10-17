import { useEffect, useRef, useState } from "react";
import type {
  PDFDocumentProxy,
  PDFDocumentLoadingTask,
  RenderTask,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { ErrorNotice, Loading } from "./components";

export function PdfPreview({
  url,
  filename,
  page,
  onPageCount,
  onRefresh,
}: {
  url: string;
  filename: string;
  page: number;
  onPageCount: (count: number) => void;
  onRefresh: () => void;
}) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [rendering, setRendering] = useState(true);
  const [width, setWidth] = useState(600);
  const canvas = useRef<HTMLCanvasElement>(null);
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    let loadingTask: PDFDocumentLoadingTask | undefined;
    const controller = new AbortController();
    setError(null);
    setPdf(null);
    void (async () => {
      const response = await fetch(url, {
        signal: controller.signal,
        credentials: "omit",
      });
      if (!response.ok)
        throw new Error(
          `原页读取失败（${response.status}）。请刷新原件访问地址后重试。`,
        );
      const data = new Uint8Array(await response.arrayBuffer());
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
      if (!active) return;
      loadingTask = pdfjs.getDocument({
        data,
        cMapUrl: "/pdfjs/cmaps/",
        cMapPacked: true,
        standardFontDataUrl: "/pdfjs/standard_fonts/",
        wasmUrl: "/pdfjs/wasm/",
        iccUrl: "/pdfjs/iccs/",
      });
      const loaded = await loadingTask.promise;
      if (!active) {
        await loadingTask.destroy();
        return;
      }
      setPdf(loaded);
      onPageCount(loaded.numPages);
    })().catch((failure: unknown) => {
      if (active) setError(failure);
    });
    return () => {
      active = false;
      controller.abort();
      if (loadingTask) void loadingTask.destroy();
    };
  }, [url, onPageCount]);
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(100, entry.contentRect.width - 32)),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!pdf || !canvas.current) return;
    let cancelled = false;
    let task: RenderTask | undefined;
    setRendering(true);
    setError(null);
    void (async () => {
      const source = await pdf.getPage(page);
      if (cancelled || !canvas.current) return;
      const base = source.getViewport({ scale: 1 });
      const viewport = source.getViewport({
        scale: Math.min(width / base.width, 2),
      });
      const target = canvas.current;
      const context = target.getContext("2d");
      if (!context)
        throw new Error("此浏览器无法绘制PDF原页。可下载原件查看。");
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      target.width = Math.floor(viewport.width * ratio);
      target.height = Math.floor(viewport.height * ratio);
      target.style.width = `${Math.floor(viewport.width)}px`;
      target.style.height = `${Math.floor(viewport.height)}px`;
      task = source.render({
        canvas: target,
        canvasContext: context,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      });
      await task.promise;
      if (!cancelled) setRendering(false);
    })().catch((failure: unknown) => {
      if (!cancelled) {
        setError(failure);
        setRendering(false);
      }
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, page, width]);
  return (
    <div className="pdf-canvas-container" ref={container}>
      {error ? (
        <ErrorNotice error={error} onRetry={onRefresh} />
      ) : (
        (!pdf || rendering) && <Loading text="正在绘制PDF原页…" />
      )}
      <canvas
        ref={canvas}
        role="img"
        aria-label={`${filename}，物理页${page}原页`}
        hidden={!pdf || !!error}
      />
    </div>
  );
}
