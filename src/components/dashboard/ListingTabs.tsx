interface ListingOption {
  id: string;
  name: string;
}

interface Props {
  listings: ListingOption[];
  selectedId: string;
  onChange: (id: string) => void;
  onMoreClick: () => void;
}

const MAX_VISIBLE = 3;

export function ListingTabs({
  listings,
  selectedId,
  onChange,
  onMoreClick,
}: Props) {
  if (listings.length <= 1) return null;

  const visible = listings.slice(0, MAX_VISIBLE);
  const hasMore = listings.length > MAX_VISIBLE;

  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.map((l) => (
        <button
          key={l.id}
          type="button"
          onClick={() => onChange(l.id)}
          className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
            selectedId === l.id
              ? "bg-foreground text-white"
              : "bg-white text-muted border border-border hover:text-foreground"
          }`}
        >
          {l.name}
        </button>
      ))}
      {hasMore && (
        <button
          type="button"
          onClick={onMoreClick}
          className="rounded-lg border border-border bg-white px-3 py-1.5 text-sm text-muted transition-colors hover:text-foreground"
        >
          + More
        </button>
      )}
    </div>
  );
}
