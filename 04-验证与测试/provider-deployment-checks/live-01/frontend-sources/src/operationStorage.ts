export function rememberOperation(bidId: string, operationId: string) {
  const storage = `tender:operations:${bidId}`;
  let previous: string[] = [];
  try {
    previous = JSON.parse(localStorage.getItem(storage) ?? "[]") as string[];
  } catch {
    /* Invalid browser cache is not a business record. */
  }
  localStorage.setItem(
    storage,
    JSON.stringify(
      [operationId, ...previous.filter((id) => id !== operationId)].slice(
        0,
        40,
      ),
    ),
  );
  window.dispatchEvent(
    new CustomEvent("operations-updated", { detail: bidId }),
  );
}
