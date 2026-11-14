<script lang="ts">
  export let name:
    | "home"
    | "search"
    | "link"
    | "queue"
    | "settings"
    | "download"
    | "folder"
    | "check"
    | "image";
  export let size = 24;
  const paths = {
    home: "m3 10 9-7 9 7v11h-6v-7H9v7H3Z",
    search: "M21 21l-6-6M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z",
    link: "m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 10a4 4 0 0 0 6 0l4-4a4 4 0 0 0-6-6l-1 1",
    queue: "M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01",
    settings: "M4 7h16M4 17h16M9 4v6m6 4v6",
    download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
    folder: "M3 6h7l2 3h9v11H3Z",
    check: "m5 12 4 4L19 6",
    image: "M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M16 7h.01",
  };
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.6"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  <path d={paths[name]} />
</svg>
