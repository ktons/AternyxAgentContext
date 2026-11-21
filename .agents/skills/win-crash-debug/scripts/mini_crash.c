/* skill 自检用最小崩溃程序: 用 `cl /nologo /Zi /Od mini_crash.c /Fe:mini_crash.exe`
 * 编译后跑 lldb_stack.py, 预期停在 deeper() 的空指针解引用并打印三层栈。
 * 注意源码不要写中文注释: cl 默认按代码页 936 读取, UTF-8 中文会触发 C4819
 * 吞掉换行导致编译错误。 */
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
