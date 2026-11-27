<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <!-- endpaper-omarchy-icon: installed by tools/omarchy-icon.sh from the Endpaper repository -->
  <title>Endpaper</title>
  <desc>Endpaper's logo in the theme's colors: an open book on its cover, the marbled endpaper on the left in the main color, a page of text on the right, and a small cut gem on it, each piece with a hard shadow.</desc>
  <defs>
    <linearGradient id="main" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="{{ icon_main }}"/>
      <stop offset="1" stop-color="{{ icon_main_end }}"/>
    </linearGradient>
    <clipPath id="left-page"><path d="M17 26c14-4 30-4 45 3v76c-15-7-31-7-45-3z"/></clipPath>
  </defs>
  <!-- Drawn on the 128 unit grid of the normal icon, made larger. -->
  <g transform="translate(256 256) scale(3.9) translate(-64 -68)">
    <!-- The cover, showing around the pages. -->
    <path d="M10 32a7 7 0 0 1 6-7c17-3 35-2 48 5 13-7 31-8 48-5a7 7 0 0 1 6 7v72a6 6 0 0 1-7 6c-16-3-33-2-47 5-14-7-31-8-47-5a6 6 0 0 1-7-6z" fill="{{ icon_shadow }}" transform="translate(0 2)"/>
    <path d="M10 32a7 7 0 0 1 6-7c17-3 35-2 48 5 13-7 31-8 48-5a7 7 0 0 1 6 7v72a6 6 0 0 1-7 6c-16-3-33-2-47 5-14-7-31-8-47-5a6 6 0 0 1-7-6z" fill="{{ icon_body }}"/>
    <!-- Left: the marbled endpaper. -->
    <g clip-path="url(#left-page)">
      <rect x="10" y="20" width="56" height="90" fill="url(#main)"/>
      <path d="M10 44c10-9 18 7 28-1s18-10 28-3v16c-10-8-18-3-28 4s-18-7-28 1z" fill="{{ icon_paper }}" opacity=".7"/>
      <path d="M10 67c10-7 18 8 28 0s18-8 28-2v12c-10-6-18-1-28 6s-18-8-28-1z" fill="{{ icon_paper }}" opacity=".9"/>
      <path d="M10 89c10-6 18 7 28 0s18-7 28-2v9c-10-5-18-1-28 5s-18-6-28 0z" fill="{{ icon_paper }}" opacity=".5"/>
      <path d="M10 33c10-6 18 5 28-1s18-6 28-2v5c-10-5-18-1-28 4s-18-5-28 0z" fill="{{ icon_shadow }}" opacity=".3"/>
      <path d="M10 56c10-5 18 5 28-1s18-6 28-2v5c-10-4-18-1-28 4s-18-5-28 1z" fill="{{ icon_shadow }}" opacity=".3"/>
      <path d="M10 79c10-5 18 5 28-1s18-5 28-1v4c-10-4-18 0-28 4s-18-5-28 0z" fill="{{ icon_shadow }}" opacity=".2"/>
    </g>
    <!-- Right: the page of text. -->
    <path d="M66 29c15-7 31-7 45-3v76c-14-4-30-4-45 3z" fill="{{ icon_paper }}"/>
    <g fill="{{ icon_ink }}">
      <rect x="74" y="38" width="28" height="4" rx="2"/>
      <rect x="74" y="50" width="29" height="3" rx="1.5" opacity=".55"/>
      <rect x="74" y="58" width="26" height="3" rx="1.5" opacity=".55"/>
      <rect x="74" y="66" width="29" height="3" rx="1.5" opacity=".55"/>
      <rect x="74" y="74" width="15" height="3" rx="1.5" opacity=".55"/>
    </g>
    <!-- The spine. -->
    <rect x="62" y="28" width="4" height="78" fill="{{ icon_shadow }}" opacity=".45"/>
    <!-- The gem: a cut stone seen from the front, its facets in the highlight. -->
    <g transform="translate(79 78) scale(0.72)">
      <polygon points="8,24 20,10 44,10 56,24 32,56" fill="{{ icon_shadow }}" transform="translate(0 3)"/>
      <polygon points="8,24 20,10 44,10 56,24 32,56" fill="{{ icon_mark }}" stroke="{{ icon_paper }}" stroke-width="5" stroke-linejoin="round"/>
      <polygon points="20,10 44,10 38,24 26,24" fill="{{ icon_paper }}" opacity=".35"/>
      <polygon points="26,24 38,24 32,56" fill="{{ icon_shadow }}" opacity=".25"/>
    </g>
  </g>
</svg>
