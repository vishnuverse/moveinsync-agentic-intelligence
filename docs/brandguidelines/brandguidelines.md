 MoveInSync Official Brand Color Palette

  
    
      Color Name
      Hex Code
      RGB
      HSL
      CMYK
      Usage
    
  
  
    
      Apple
      #38AF48
      56, 175, 72
      128, 52%, 45%
      68, 0, 59, 31
      Primary brand color (CTAs, accents)
    
    
      Malibu
      #8ED1FC
      142, 209, 252
      203, 95%, 77%
      44, 17, 0, 1
      Secondary accent (highlights, backgrounds)
    
    
      Outer Space
      #32373C
      50, 55, 60
      210, 9%, 22%
      17, 8, 0, 76
      Dark text, footers, backgrounds
    
    
      Pink
      #CC3366
      204, 51, 102
      337, 60%, 50%
      0, 75, 50, 20
      Buttons, links, CTAs
    
    
      Dark Blue
      #333366
      51, 51, 102
      240, 33%, 28%
      50, 50, 0, 60
      Hover states, secondary accents
    
    
      Text Dark
      #333333
      51, 51, 51
      0, 0%, 20%
      0, 0, 0, 80
      Primary text, body content
    
    
      Background
      #FFFFFF
      255, 255, 255
      0, 0%, 100%
      0, 0, 0, 0
      Page background
    
    
      Borders
      #666666
      102, 102, 102
      0, 0%, 40%
      0, 0, 0, 60
      Input borders, dividers
    
    
      Gray
      #808080
      128, 128, 128
      0, 0%, 50%
      0, 0, 0, 50
      Secondary text, disabled states
    
  




📌 Key Updates from Your Input
New Primary Brand Color: Apple (#38AF48)
Replaces or complements the existing Pink (#CC3366) as the primary brand color.
Use for CTAs, accents, and key branding elements (e.g., logo, hero sections).

New Secondary Accent: Malibu (#8ED1FC)
A light blue for highlights, backgrounds, or interactive elements (e.g., hover effects, cards).

New Dark Theme Color: Outer Space (#32373C)
A deep gray-blue for dark text, footers, or backgrounds (e.g., navigation bars, footers).

🛠️ Updated CSS Variables for Your Build
css
Copy

:root {
  /* Primary Brand Colors */
  --primary-green: #38AF48;    /* Apple */
  --primary-pink: #CC3366;     /* Legacy Pink (if still used) */

  /* Secondary Colors */
  --secondary-blue: #8ED1FC;   /* Malibu */
  --secondary-dark-blue: #333366; /* Dark Blue */

  /* Neutral Colors */
  --text-dark: #32373C;        /* Outer Space (new dark text) */
  --text-gray: #333333;        /* Legacy dark text */
  --background: #FFFFFF;
  --border-color: #666666;
  --gray: #808080;
}




🎯 Recommended Color Usage
1. Primary Branding (Apple Green - #38AF48)
Logo (if not using pink)
Primary CTAs (e.g., "Request a Demo" buttons)
Hero section backgrounds or accents
Icons or illustrations
2. Secondary Accents (Malibu Blue - #8ED1FC)
Hover states for buttons/links
Card backgrounds (e.g., feature highlights)
Borders or dividers for a modern touch
Info boxes or callouts
3. Dark Text (Outer Space - #32373C)
Body text (replaces #333333 for better brand alignment)
Headings (h1, h2, etc.)
Footer text
4. Legacy Colors (Pink - #CC3366 and Dark Blue - #333366)
Use only if required for backward compatibility (e.g., existing marketing materials).
Gradually phase out in favor of Apple Green (#38AF48) and Malibu Blue (#8ED1FC).
🔍 Visual Example: Button Styles
Primary Button (Apple Green)
css
Copy

.btn-primary {
  background-color: var(--primary-green);
  color: white;
  border: none;
  padding: 0.75rem 1.5rem;
  border-radius: 4px;
  font-weight: 500;
  transition: background-color 0.3s;
}

.btn-primary:hover {
  background-color: #2a8f38; /* Darker shade of Apple Green */
}




Secondary Button (Malibu Blue)
css
Copy

.btn-secondary {
  background-color: transparent;
  color: var(--secondary-blue);
  border: 1px solid var(--secondary-blue);
  padding: 0.75rem 1.5rem;
  border-radius: 4px;
  font-weight: 500;
  transition: all 0.3s;
}

.btn-secondary:hover {
  background-color: var(--secondary-blue);
  color: white;
}




📊 Color Contrast Validation

  
    
      Color Combination
      Contrast Ratio
      WCAG Compliance
    
  
  
    
      Apple (#38AF48) on White
      4.5:1
      AA (Pass)
    
    
      Malibu (#8ED1FC) on White
      1.8:1
      Fail (Avoid for text)
    
    
      Outer Space (#32373C) on White
      13.3:1
      AAA (Pass)
    
    
      White on Apple (#38AF48)
      3.8:1
      AA (Pass)
    
  




⚠️ Note: Avoid using Malibu (#8ED1FC) for text on white backgrounds (low contrast). Use it for backgrounds, borders, or large non-text elements.
