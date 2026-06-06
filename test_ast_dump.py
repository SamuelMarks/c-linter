import os
from c_linter.linter import Index, CursorKind

code = """
#include <stdlib.h>
int main(void) {
    void *p = malloc(10);
    if (!p) return 1;
    void *q = calloc(1, 10);
    if (q == NULL) return 1;
    void *r = realloc(p, 20);
    if (NULL != r) { }
    return 0;
}
"""
with open("memory.c", "w") as f:
    f.write(code)

index = Index.create()
tu = index.parse("memory.c", args=["-std=c89"])

def print_ast(cursor, depth=0):
    print(f"{'  '*depth}{cursor.kind.name} | {cursor.spelling}")
    for child in cursor.get_children():
        print_ast(child, depth+1)

for child in tu.cursor.get_children():
    if child.kind == CursorKind.FUNCTION_DECL and child.spelling == "main":
        print_ast(child)
