/* Minimal crash fixture for validating the stack-capture chain:
 *   cl /nologo /Zi /Od mini_crash.c /Fe:mini_crash.exe
 * It should stop at the null dereference in deeper() and print a three-frame stack.
 * Keep this file ASCII: cl reads source in the system code page (936 on zh-CN),
 * and UTF-8 comments can trigger C4819 or swallow line breaks. */
#include <stdio.h>

int* g_ptr = NULL;

int deeper(int x) { return *g_ptr + x; } /* null deref = crash point */
int middle(int x) { return deeper(x * 2); }
int main(void) {
    printf("before crash\n");
    int r = middle(21);
    printf("%d\n", r);
    return 0;
}
