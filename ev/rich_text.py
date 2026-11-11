"""Safe scientific text: x_{i} and x^{2}, without executing user HTML."""
import html
import re
import unicodedata


def scientific_html(value, depth=0):
    text = str(value if value is not None else "")
    if depth >= 6:
        return html.escape(text)
    pairs, stack = {}, []
    for position, char in enumerate(text):
        if char == "{":
            stack.append(position)
        elif char == "}" and stack:
            pairs[stack.pop()] = position
    result, start, index = [], 0, 0
    while index < len(text)-1:
        if text[index] in "_^" and text[index+1] == "{":
            closing = pairs.get(index+1)
            if closing is not None:
                end = closing+1
                tag = "sub" if text[index] == "_" else "sup"
                result.extend((html.escape(text[start:index]), f"<{tag}>",
                               scientific_html(text[index+2:end-1], depth+1), f"</{tag}>"))
                start = index = end
                continue
        index += 1
    result.append(html.escape(text[start:]))
    return "".join(result)


def search_text(value):
    plain = html.unescape(re.sub(r"</?(?:sub|sup|strong)>", "", notes_html(value)))
    return unicodedata.normalize("NFKC", plain).replace("−", "-").casefold()


def notes_html(value):
    """Only **bold** plus the existing sub/sup syntax; arbitrary HTML stays text."""
    parts = str(value if value is not None else "").split("**")
    result = [scientific_html(parts[0])]
    for index in range(1, len(parts), 2):
        if index + 1 < len(parts) and parts[index]:
            result.extend(("<strong>", scientific_html(parts[index]), "</strong>", scientific_html(parts[index + 1])))
        else:
            result.append(scientific_html("**" + "**".join(parts[index:index+2])))
    return "".join(result)
