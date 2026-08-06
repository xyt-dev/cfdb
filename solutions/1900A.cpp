// 1900A Cover in Water — C++ 版本
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int t; cin >> t;
    while (t--) {
        int n; string s; cin >> n >> s;
        int empty = count(s.begin(), s.end(), '.');
        bool three = false;
        for (int i = 0; i + 2 < n; i++)
            if (s[i] == '.' && s[i+1] == '.' && s[i+2] == '.') three = true;
        cout << (three ? 2 : empty) << '\n';
    }
    return 0;
}
