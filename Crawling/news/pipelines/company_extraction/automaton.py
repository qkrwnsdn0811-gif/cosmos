"""Small dependency-free Aho-Corasick trie; scan cost O(text + matches)."""

from collections import deque


class AhoCorasick:
    def __init__(self, patterns):
        self.next = [{}]
        self.fail = [0]
        self.outputs = [[]]
        for pattern, payload in patterns:
            if not pattern:
                continue
            state = 0
            for char in pattern:
                if char not in self.next[state]:
                    self.next[state][char] = len(self.next)
                    self.next.append({})
                    self.fail.append(0)
                    self.outputs.append([])
                state = self.next[state][char]
            self.outputs[state].append((len(pattern), payload))
        queue = deque(self.next[0].values())
        while queue:
            state = queue.popleft()
            for char, nxt in self.next[state].items():
                queue.append(nxt)
                fallback = self.fail[state]
                while fallback and char not in self.next[fallback]:
                    fallback = self.fail[fallback]
                self.fail[nxt] = self.next[fallback].get(char, 0)
                self.outputs[nxt].extend(self.outputs[self.fail[nxt]])

    def find(self, text):
        state = 0
        for end, char in enumerate(text, 1):
            while state and char not in self.next[state]:
                state = self.fail[state]
            state = self.next[state].get(char, 0)
            for length, payload in self.outputs[state]:
                yield end - length, end, payload
