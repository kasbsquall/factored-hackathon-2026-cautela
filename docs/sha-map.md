# Commit SHA map (history rewrite before publication)

Before publication the history was rewritten with git filter-repo (author emails normalized, organizer-derived
case files and report values removed), so every commit SHA changed. Files committed before the rewrite cite
the old SHAs and were left as they are on purpose: `eval/fresh/manifest.json` is hash-checked, and the result
files record the commit each run used. Use this table to find the commit that an old SHA refers to.

## SHAs cited in the repository

| old | new | commit | cited in |
|---|---|---|---|
| `87393706ebd6` | `ba9bb6fbd19b` | feat: add deterministic dispute policy engine with sourced rules | `ml/reports/experiments.md:181`, `ml/reports/experiments.md:182`, `ml/reports/experiments.md:183`, `ml/reports/experiments.md:184`, `ml/reports/experiments_runs.json:11`, `ml/reports/experiments_runs.json:156`, `ml/reports/experiments_runs.json:43`, `ml/reports/experiments_runs.json:82` |
| `840124ec736d` | `7e7c2b0ad464` | test: add 24 team-generated adversarial security cases in Spanish and Portuguese | `ml/reports/experiments.md:185`, `ml/reports/experiments.md:186`, `ml/reports/experiments.md:187`, `ml/reports/experiments.md:188`, `ml/reports/experiments.md:189`, `ml/reports/experiments.md:190`, `ml/reports/experiments.md:191`, `ml/reports/experiments.md:192`, `ml/reports/experiments.md:193`, `ml/reports/experiments_runs.json:230`, `ml/reports/experiments_runs.json:275`, `ml/reports/experiments_runs.json:342`, `ml/reports/experiments_runs.json:413`, `ml/reports/experiments_runs.json:484`, `ml/reports/experiments_runs.json:556`, `ml/reports/experiments_runs.json:628`, `ml/reports/experiments_runs.json:704`, `ml/reports/experiments_runs.json:780` |
| `96dd7539cc65` | `e36549afb9fd` | docs: add dictionary-vs-data reconciliation and real-data observations | `ml/reports/experiments.md:194`, `ml/reports/experiments.md:195`, `ml/reports/experiments.md:196`, `ml/reports/experiments.md:197`, `ml/reports/experiments.md:198`, `ml/reports/experiments.md:199`, `ml/reports/experiments.md:200`, `ml/reports/experiments.md:201`, `ml/reports/experiments.md:202`, `ml/reports/experiments.md:203`, `ml/reports/experiments.md:204`, `ml/reports/experiments.md:205`, `ml/reports/experiments.md:206`, `ml/reports/experiments_runs.json:1050`, `ml/reports/experiments_runs.json:1126`, `ml/reports/experiments_runs.json:1203`, `ml/reports/experiments_runs.json:1275`, `ml/reports/experiments_runs.json:1351`, `ml/reports/experiments_runs.json:1427`, `ml/reports/experiments_runs.json:1504`, `ml/reports/experiments_runs.json:1576`, `ml/reports/experiments_runs.json:1652`, `ml/reports/experiments_runs.json:1728`, `ml/reports/experiments_runs.json:857`, `ml/reports/experiments_runs.json:902`, `ml/reports/experiments_runs.json:974` |
| `8fbdee740611` | `61c04fbb9d99` | fix(eval): keep dispute case files LF so manifest hashes hold on Windows checkouts | `ml/reports/experiments.md:207`, `ml/reports/experiments.md:208`, `ml/reports/experiments.md:209`, `ml/reports/experiments.md:210`, `ml/reports/experiments.md:211`, `ml/reports/experiments.md:212`, `ml/reports/experiments.md:213`, `ml/reports/experiments.md:214`, `ml/reports/experiments.md:215`, `ml/reports/experiments.md:216`, `ml/reports/experiments.md:34`, `ml/reports/experiments.md:60`, `ml/reports/experiments.md:61`, `ml/reports/experiments.md:62`, `ml/reports/experiments.md:63`, `ml/reports/experiments_runs.json:1805`, `ml/reports/experiments_runs.json:1850`, `ml/reports/experiments_runs.json:1922`, `ml/reports/experiments_runs.json:1998`, `ml/reports/experiments_runs.json:2074`, `ml/reports/experiments_runs.json:2150`, `ml/reports/experiments_runs.json:2195`, `ml/reports/experiments_runs.json:2267`, `ml/reports/experiments_runs.json:2343`, `ml/reports/experiments_runs.json:2419` |
| `63163f5782be` | `b98b6d8d1f96` | docs(ml): refresh results and README for the rebuilt cases | `ml/reports/parser_readback_before.json:2`, `ml/reports/results.md:353` |
| `6f3c4e10e247` | `bfaaf57adef0` | feat: add provider-agnostic LLM port with masking enforced before adapters | `ml/reports/experiments.md:217`, `ml/reports/experiments.md:218`, `ml/reports/experiments.md:219`, `ml/reports/experiments.md:220`, `ml/reports/experiments.md:221`, `ml/reports/experiments.md:222`, `ml/reports/experiments.md:223`, `ml/reports/experiments.md:224`, `ml/reports/experiments.md:225`, `ml/reports/experiments.md:33`, `ml/reports/experiments.md:42`, `ml/reports/experiments.md:43`, `ml/reports/experiments.md:44`, `ml/reports/experiments.md:45`, `ml/reports/experiments_runs.json:2495`, `ml/reports/experiments_runs.json:2541`, `ml/reports/experiments_runs.json:2613`, `ml/reports/experiments_runs.json:2689`, `ml/reports/experiments_runs.json:2765`, `ml/reports/experiments_runs.json:2841`, `ml/reports/experiments_runs.json:2913`, `ml/reports/experiments_runs.json:2989`, `ml/reports/experiments_runs.json:3065` |
| `4903a3821ec5` | `9473c080713e` | fix(agent): ground model extractions and fail safe on orchestrator defects | `ml/reports/experiments.md:226`, `ml/reports/experiments.md:227`, `ml/reports/experiments.md:228`, `ml/reports/experiments.md:229`, `ml/reports/experiments.md:51`, `ml/reports/experiments.md:52`, `ml/reports/experiments.md:53`, `ml/reports/experiments.md:54`, `ml/reports/experiments_runs.json:3141`, `ml/reports/experiments_runs.json:3212`, `ml/reports/experiments_runs.json:3289`, `ml/reports/experiments_runs.json:3365` |
| `3647a2fc81a3` | `97614d1e31b7` | docs(deploy): record measured memory and build time | `deploy/README.md:117`, `deploy/README.md:137`, `deploy/README.md:283`, `ml/reports/experiments.md:230`, `ml/reports/experiments.md:33`, `ml/reports/experiments_runs.json:3440` |
| `0c6fbebaa298` | `57ed13d614ae` | fix(deploy): ship the handoff schema and refuse to start without it | `README.md:243`, `deploy/README.md:48` |
| `0fc526bdc8e5` | `d9c3c3b34255` | fix(app): type-check only the app in the production build | `README.md:28`, `README.md:310`, `eval/fresh/DATASHEET.md:3`, `eval/fresh/manifest.json:16` |
| `785029b95f9f` | `4f9e321b46d8` | fix(agent): stop the reply guard from rejecting correct LLM drafts | `eval/fix_rounds.py:31`, `eval/report.md:774`, `eval/report_template.md:453`, `eval/results_fix_rounds.json:6` |
| `be506c486804` | `c4e40bbf334a` | fix(agent): abstain only when no_match is the most likely class | `eval/fix_rounds.py:31`, `eval/report.md:774`, `eval/report_template.md:453`, `eval/results_fix_rounds.json:6` |
| `80c21fd13446` | `9cbc2043bfa1` | feat(agent): es/pt request lexicon, injection detector and parser-first LLM merge | `eval/fix_rounds.py:31`, `eval/report.md:774`, `eval/report_template.md:453`, `eval/results_fix_rounds.json:6` |
| `773a62b60a00` | `d1d2b0a596e3` | fix(agent): route every intent at every stage and screen security first | `eval/fix_rounds.py:31`, `eval/report.md:775`, `eval/report_template.md:454`, `eval/results_fix_rounds.json:6` |
| `55222bca5213` | `8b68dea6ed24` | fix(agent): note a new request after a handoff without replacing its reason | `eval/fix_rounds.py:31`, `eval/report.md:1023`, `eval/report_template.md:563`, `eval/results_fix_rounds.json:6` |
| `672c33da9a4d` | `f175cbacc2ec` | fix(agent): bound clarifying rounds and hand off with what was gathered | `eval/fix_rounds.py:32`, `eval/report.md:1024`, `eval/report.md:1044`, `eval/report.md:775`, `eval/report_template.md:454`, `eval/report_template.md:564`, `eval/report_template.md:570`, `eval/results_fix_rounds.json:2758` |
| `4f630cc16d66` | `fd4c5e07d368` | fix(agent): show the charges a description could mean before abstaining | `eval/fix_rounds.py:32`, `eval/report.md:1025`, `eval/report.md:1049`, `eval/report.md:775`, `eval/report_template.md:454`, `eval/report_template.md:565`, `eval/report_template.md:575`, `eval/results_fix_rounds.json:2758` |
| `f0b343cf612b` | `2097fdfdcb82` | docs: write the final README for judges | `ml/reports/experiments.md:231`, `ml/reports/experiments.md:232`, `ml/reports/experiments.md:233`, `ml/reports/experiments.md:234`, `ml/reports/experiments.md:87`, `ml/reports/experiments.md:95`, `ml/reports/experiments.md:96`, `ml/reports/experiments.md:97`, `ml/reports/experiments_runs.json:3486`, `ml/reports/experiments_runs.json:3553`, `ml/reports/experiments_runs.json:3620`, `ml/reports/experiments_runs.json:3687` |
| `916d4ae3271a` | `f3be97c03d6e` | fix(eval): load the learned configuration the way the service does | `ml/reports/experiments.md:235`, `ml/reports/experiments.md:236`, `ml/reports/experiments.md:237`, `ml/reports/experiments.md:238`, `ml/reports/experiments.md:239`, `ml/reports/experiments.md:240`, `ml/reports/experiments.md:241`, `ml/reports/experiments.md:242`, `ml/reports/experiments.md:69`, `ml/reports/experiments.md:70`, `ml/reports/experiments.md:71`, `ml/reports/experiments.md:72`, `ml/reports/experiments.md:78`, `ml/reports/experiments.md:79`, `ml/reports/experiments.md:80`, `ml/reports/experiments.md:81`, `ml/reports/experiments_runs.json:3784`, `ml/reports/experiments_runs.json:3860`, `ml/reports/experiments_runs.json:3935`, `ml/reports/experiments_runs.json:4007`, `ml/reports/experiments_runs.json:4079`, `ml/reports/experiments_runs.json:4157`, `ml/reports/experiments_runs.json:4235`, `ml/reports/experiments_runs.json:4313` |
| `017293e087a9` | `4101cdb044b4` | feat(eval): state the safe-resolution ceiling in every breakdown | `README.md:291`, `eval/llm_extraction.json:6`, `eval/report.md:793`, `eval/report.md:794`, `eval/report.md:795`, `eval/report.md:796`, `eval/results_after_fix.json:10`, `eval/results_after_fix.json:17774`, `eval/results_after_fix.json:26666`, `eval/results_after_fix.json:8947`, `ml/README.md:221` |
| `b56da1bf87b2` | `50c43a165f55` | feat(eval): repeat the high-risk categories and measure run-to-run agreement | `eval/report.md:1159`, `eval/results_repeats.json:27`, `eval/results_repeats.json:286`, `eval/results_repeats.json:545` |
| `2da6b8d17c26` | `3673d07f216d` | feat(eval): noisy-customer slice generator with six noise families | `eval/noisy/dev/manifest.json:21` |
| `71fff14b42c3` | `da413fe0051f` | feat(eval): runner for the noisy slice with safe handoff reported apart | `eval/noisy/DATASHEET.md:55`, `eval/noisy/dev/results.json:14`, `eval/noisy/dev/results.json:3482`, `eval/noisy/dev/results.json:7834` |
| `e2dcb786d90e` | `b1b91aa7b6a5` | feat(eval): sealed noisy-customer templates written blind to the dev results | `README.md:349` |
| `e78641fb6f22` | `1244e7ebc749` | fix(agent): read month abbreviations and numeric dates in the parser | `README.md:369` |
| `9bcc384c1c50` | `f6188150eefa` | fix(agent): a repaired value wins over the one it replaces | `README.md:369` |
| `21afb3b61454` | `3668f1409300` | fix(agent): keep explicit dates out of the ranker text as a placeholder | `README.md:369` |
| `de5119b9dbb3` | `e57ef4af937a` | fix(agent): ask with options when one cue of two is a detail off | `README.md:369` |
| `ee9ea5afe67e` | `5458d33e4226` | fix(agent): prefer a charge that moved money over a declined one | `README.md:369` |
| `6f05c06e536f` | `0de0a3210f5c` | fix(agent): a charge detail stays a dispute while details are awaited | `README.md:369` |
| `fe455b7d612c` | `fbe3d577254f` | fix(agent): hand off with the filed case when a person is asked after filing | `README.md:117` |
| `04fa70828d78` | `9b352ea3dccf` | fix(agent): keep a resolve whose runner-up was rejected | `README.md:369`, `eval/noisy/dev/results_after_noisy_fix.json:14`, `eval/noisy/dev/results_after_noisy_fix.json:3318`, `eval/noisy/dev/results_after_noisy_fix.json:6749`, `eval/results_after_noisy_fix.json:10`, `eval/results_after_noisy_fix.json:17778`, `eval/results_after_noisy_fix.json:8949` |
| `829a64bc453e` | `8dfe2d27eabd` | feat(eval): heldout and noisy dev results after the noisy-slice fixes | `README.md:28`, `README.md:315`, `eval/fresh/ran/learned.json:9`, `eval/fresh/ran/rules.json:9` |
| `df59080d3897` | `d52f37a8d28d` | feat(eval): eval_fresh results for rules and learned, run once on the frozen code | `README.md:315`, `eval/fresh/ran/llm.json:9` |
| `d205e45ed18e` | `fa40bc7f56e4` | feat(eval): eval_fresh result for llm, run once on the frozen code | `eval/noisy/sealed/manifest.json:22` |
| `4287d8b85e16` | `bddb9708ca96` | feat(eval): freeze the sealed noisy half (240 conversations from test_fresh) before any run | `eval/noisy/sealed/results.json:14`, `eval/noisy/sealed/results.json:3443`, `eval/noisy/sealed/results.json:7011` |

## Full map

| old | new |
|---|---|
| `003476b0554d8dac97657c6605120e72a0e4f7ec` | `2a90a5c58bffe5b70d915787cd70c0d7c704079d` |
| `017293e087a96d1f514209e1917897870553cfc3` | `4101cdb044b4c050448456cef1d3ea27447bf720` |
| `01a3b5666e81b31c1955f23cf399b92132e7cfa7` | `c2b2d0aaf4b7388b19b26befea0d0490eb11256b` |
| `02cdccfa0c21a66989987045b912cbdb4435e361` | `b6cf0ca749e10cc3cc48f54b08651e5cebfc0b18` |
| `04fa70828d78dabea7fcf977701d724686033106` | `9b352ea3dccf2d6142aa760e1f1c4a54d175e57c` |
| `056e252edd111218a2fac498d3cd417e0fccfc59` | `fd50924f7a6df07d48e405928d2d9bad9685d0c1` |
| `08edd7f964c7377df319f4405f31504cf80667c3` | `3edcc77abfb7fe610a32d06e88a3e38f2afe8f45` |
| `0c25d874e057ba8f1f3f891c1be391c094180d5a` | `5d8383046dcfe39df8dca71f5fc7a205c93037bf` |
| `0c6fbebaa2982908a313bbc46d5f028de72e0631` | `57ed13d614ae4d8b802d2115c5680f95abeed4fb` |
| `0d96ae97fa1e7a49714cd452223dcd847f0cc4ad` | `4de790dfedecc8fd5bc635a65f5a1d57a479023f` |
| `0e90415ead9a510d096cff8c04f583bef6069aa3` | `91254689a1e160870030b736fd93812bf11b8b8c` |
| `0f17897f2016039c3e4ea2001281010f1f6e62db` | `4a4ebca7bdee9e4651768165c11ecad6e42bedb6` |
| `0fc526bdc8e55ca1edbaebcc9c298fc54eeec2d1` | `d9c3c3b342557e3112f021cf55fbe3d1b3cf3ca6` |
| `13cd653266da4edfa66a23d43a649dcabeeaaa45` | `c597cc944c07e12b8896b294dfc91b6183ea2413` |
| `16561fdeb4bed35656ce3b24a16445fce96e781d` | `6deb27b68ec0d06906e657c33e938e63faaaae64` |
| `17cc62d332a85e807ba83718d35f1bcf9ce7b3a8` | `07b3477e3e91e92c46d78c3662c386585a88a8f4` |
| `1add258de106e21a82daf98229332018cc6c2542` | `fe3bb77c1680f17bde00b4c1cd5878a4e4a5cf5d` |
| `1ba5243954ded749f0c578b2d84fba56dde57e73` | `3cc05d768b6e3bdfbc3cbac48fdad067fd259df5` |
| `1c336e6fee4b0fb210b78bd2227be738224a2615` | `d3ee903d8c491cf178c85cb8c5df56d39e90c8e2` |
| `1cb36b3df2907d8ba0d928a199efadefcbc1864f` | `73affda224f4996c264400ed3ee8d65f8099ca7e` |
| `1d86d968b534c617253038b21669bfce8fb46d45` | `43ad66ee811d099e71c34b1adc9722955dd5f243` |
| `209d935da6c43afe8fd6e19baaa52f9e95355d34` | `75f70a5f5eaa803d47ccd2075ee6bcb88f4815f0` |
| `21afb3b61454f6fddcf2386c07512d5a24d8377b` | `3668f14093001d7c710d846709e9b67dc7be0fb6` |
| `23b9c4d30c35709c96ae0b23967702931b783228` | `c18f7ded56905a3b43be5702af05b13d21e1e788` |
| `2683ec4e15b1f44ce7db5969b19650bcb715c155` | `6f3270b09237b42eccba208e07d5f0b2e7a0fabf` |
| `282e0ced8a2a29337403c7ff0deed08c5e5277a5` | `6f708a4bfbb96966958fa6784383721493d52d93` |
| `285a59d9827032afb28772a076f8fb0a91e7fbec` | `b628fde4f87f401c1ff788483ca5964ee4b48987` |
| `2b64f788d3979d6ef6f7866d8faf0d2857bef93d` | `9633c9810136553105c7162058461c4b3fe9c543` |
| `2da6b8d17c261f5488badd8ac8d59d22b0869566` | `3673d07f216d50fa2626960488dcea5b783457dc` |
| `2ec2f7226ab880e2b645cd6b4d3379a49244c8f7` | `77ba18c01570509b3c3fab741649dfee8388ce4c` |
| `2ec8467172a2065f727dc781b213fe3e32f2d642` | `b211759021ae0666859a884b4fd1e62f24641689` |
| `333c0eeff1f41e7a33db656c025801fcecb2c837` | `4e2f702a6bfd1d1d4f146fd1aaa83aa26d75fe12` |
| `33cff753c7ae2caead404b894bae7bf3b959b01e` | `ce6a3fd840da0784b18cabc4d268312d0d230a74` |
| `3494b1ced934df545f11f0f845057cf52b40f101` | `74353e4875d523b8629184587bf27bb913da19e7` |
| `35a202fd057bdf497470dd060c4eaf8396be2587` | `f3832abaeb3f9e93b269b0f36cbfc2b3942d612b` |
| `3647a2fc81a3d38b1a5d1908b4d2c154b087899c` | `97614d1e31b772a5e3061fc8138bfba316b6e119` |
| `37f03f8fd104d7870406d2018f94f336afb0dd6c` | `92cd4a246dec0053b9fd01162f53d357770235af` |
| `38171d0bb70f365994cf621ed3e91ac5409bed37` | `c32bc7f3cfa8b357aab35b94e0648540e8c6e57f` |
| `3896896baf5f973f16f059fed84c3fc4b44aa246` | `ef343afeb02792bbdd4517899f96baf0f09824ac` |
| `39e08124c486c8b2b744ca94a711d32e37d21848` | `52895bfd920efd0d6461ba3ef1fc9994b3eaa23f` |
| `3aeaa293bd6b33b2f71e25fb2e4bd7ba66711cf4` | `26f3ff6d1672cf6836f6eef60a2c09fd0397d4ff` |
| `3b302b50cd1adabd1a9311530b00e13190eeec85` | `c0212b3c9f0d319f6b3152a25cb494f0d9d26e93` |
| `3bf3a5a5d04ff15b49d1285ae907e31c1023807f` | `632874cef25e84bc7dd22fe56e114ae90eb0ae7f` |
| `3d9e7f8f4aba77d3861041b26706fac39bab557c` | `83315f08c1b96c2842e74001f8d5bdbee169cbfb` |
| `3f26001faec45721c3a63472ca788db13ec7f9e7` | `75016d2a6c806fcbbde42bb61d952608c62f46ab` |
| `40fe7ddfe8993da2d2ce645fde5439a5fa501567` | `25e700d717ef2ad8a77429fcc7296199a9328985` |
| `41c8af57bb18b16c47edfe8b40903d694389d0c6` | `ff35f0d0a39c778085c350721de89977498eeb8d` |
| `427a6af35a8fb788908a7be2cc18c0e5cfb40fd3` | `46659d0409be70830f887adac4414a40b47e4920` |
| `4287d8b85e162e9b5b390742560b8b0f8d99359c` | `bddb9708ca96c08dc484d58ebd0c2f2965abda22` |
| `44b22bee359855f3b9c122f797d46accc4e80920` | `87683f06a1ea3c13ef6cd9edd6aaed537bd95ecd` |
| `451d9bad84f715c298a7cb270d74084d54d43620` | `b0b0a8a70b2b64772ad6bcc90db11415f64f52a8` |
| `4683e091e35f613b13e8c7ea7f4a19ca194ebba2` | `482f52fe84dd83e8b46bce40d04e9f52354336bb` |
| `46d95ccc04c965badfc1523794e1915ce3516bb3` | `d69b8cf580d87e041305dcb26566bffba4d85427` |
| `4903a3821ec5f1b83ad7438c5e18daf74a5a4d22` | `9473c080713e5687ffa68d486ba0113fb4a89f96` |
| `4b883aa9d9f7480aef0062ec9b9e0388ff7c6498` | `a12430271f760bd325b26869008a03a7bf3fa736` |
| `4cd6c1867ad9d1187af6beb9e78d89283801889f` | `541455df5150b59529d20709a02698a949df0805` |
| `4ec25c700a196eb46cbe6683e5d92294d4b484ee` | `fbcc3c11a654ed7ec66136c91b3cb013c4151ace` |
| `4f630cc16d662e17cf4f6d156dee015a3f529ce0` | `fd4c5e07d3689607ca5168e704deb2107a883ec9` |
| `5105947d7f1e7fa35833c31b39296a50e0f3ee8c` | `7834a212e628f36d8e409a3e4a516c261a9a77f2` |
| `513586d4bfc3de365070bb5cf72b86556cae4856` | `8988b443023336d9150d65e369817cf7ec299e82` |
| `515dc9ce96db8ab1deeb144d2d6476e3ad298d3e` | `588a2e91c3af9cc21d062e3eecd1d1ae30079dea` |
| `51df0a3bbb03203a38a5ed392711e44d94467071` | `378eff96a825507eb4a93e8677058fa1b942c598` |
| `52576731b90ddd35b4736bf8519b0b043a7c0884` | `2723a69814255326d4333cb6e8a8ae5cf40c6896` |
| `526e6b53dea32701db98f8dfd9cfda5ea04a3ae6` | `12b9d3a261d8c7ed8bed8585588d6e0a0d83fdf6` |
| `55222bca5213afb9db35f89524489af81ccf6d88` | `8b68dea6ed24956dbc632cb3c93d4294687c6353` |
| `55baf3c41872b5b5e3cdda1ffc534f32eb5ab84e` | `bc392cd69c175fb613d897cb0f23e7e4accff453` |
| `59a80bfa7f0c7a76c4d2106a21dd42e8d93a6859` | `f3c1d53dea1f1e9302c2a5ab59f544d3201c9837` |
| `5a2a368a077c1a2dc1ada9ccba6362c800904727` | `a7b2c1b1951089a5ac59c7b87de986ff1e364d9c` |
| `5ab5896ec3b98d2863892d24d26a2e7cbe65f0b4` | `e301157abec8fb8225c3ac8191b8294466b8b314` |
| `5db92304302aab75115f4255940fa709c9dccf84` | `3b306fdb38f8d236dce97f2c7aeb8bd9f969cc1f` |
| `5ee794274d9ef6d28ba63f7141673a37832f23fc` | `dc20310868dd06d6be01e31383937b119c9961c6` |
| `63163f5782be027eeff4f31f9f96298fb28887e6` | `b98b6d8d1f96b80a3aafacb16bb5a009d959f19f` |
| `672c33da9a4d4e17e3f92083df77717b32941404` | `f175cbacc2ec2847cd4fdf06e296180ad952c0d2` |
| `6994a53748c7997b71bbfc733dbfe66554244697` | `e8f52b30b501726e4f79822541c1e007b696dcd6` |
| `6a71fad2dae4c4e6e5aa0bb1d414268689af5335` | `54b203c21062bfa1ed501ff353a3160f522c55ec` |
| `6d83fbbfc5572579c055f6b1aa3154b4f39ce81a` | `31bf929faebf3912815fdc22e1409bc81b5d47f6` |
| `6dbc776e22e071ac62a3adab129dcf301762db4b` | `4608eaf342df80a38d7d007eea3c2ce486410cdf` |
| `6f05c06e536fe5feaad3901130e0d2678da5ed73` | `0de0a3210f5c7f9b1fee45282ff86493037218fe` |
| `6f3c4e10e247ccf4099b511dee8c351a34a843cd` | `bfaaf57adef0682723c6f88f5a79a2ac3fca404f` |
| `71fff14b42c3710b8710e5b29d026481380ee08b` | `da413fe0051f83e9c8d80f52800d354fd28f55b2` |
| `7210c8b3fdb2866d030511071ff4cb2b1ef4d3ba` | `0a57c92e9b2d37e0b730ebd34556be5717f78a9f` |
| `72b8bcef1cae651f67476797a0629dbf960e8cf3` | `724047bf90199f9dcaf5106e0718e421a333e971` |
| `773a62b60a009eb9e60cc1313ff1263c14d799cc` | `d1d2b0a596e35057aff18f5a024e08d75c5ac41f` |
| `78216757724a07d32229a16b5f9e0998b4f30de7` | `c18091e66dd98374a16392ab851080416f072828` |
| `785029b95f9f6dbabc06c4554bc1030f6c251365` | `4f9e321b46d8c40a0540bf417cf12f0fcfa727c1` |
| `78b6a43eedaa3be9576306823e1d742a4cb3b1f3` | `d014dc27415e8d0df6ed14403091703d10255817` |
| `7a31f51f5395f80ab9288a0ad0531e95cbb9d614` | `0e3c05b17f28f27b083d693fe3666b66190e8f31` |
| `7b5aac282a75025df16499dc1b235967722af74f` | `125171dc78cf33b25170a42b967171bcc618e822` |
| `7d17902c1adcb0036471b6204419dbe424ce3d6b` | `b10b411e235cf58584b08370066313fe7462321c` |
| `7db331639003d296cc3a28c8fc790b544001947d` | `ec894f238902593e322b77de4c71a8f2942a86ec` |
| `80c21fd1344648acce8ef36f9cb0febc85ebc1c1` | `9cbc2043bfa1c53622821507ef09c9aea9f2d909` |
| `81663057e50f88d6c583ac094eafca11a2a94954` | `9298aa1b71345b0ebe42ef21fbf40a441e352fed` |
| `821419c195edcae85516c694eec700c605a4e8fa` | `7a2a0beb855c3d8ef47dd443cba8283b8582b0ef` |
| `829a64bc453eda5b647c8131bb3b2e3039b61c52` | `8dfe2d27eabdc67c2cc8b1400627b31cc0a9c7cf` |
| `82d73296b9bfcaf0fa93481b4780cff4b8247570` | `6dc91ae9066b6dd32d499f67981bf7125cf6e94f` |
| `83b972d2a4cf807e6195f89384d968b225752d2d` | `a57ae272d0df5ae205c4c77afb75daa156db09c0` |
| `840124ec736df4af3640696478614c7cffa24b47` | `7e7c2b0ad4647c1c4b98d02d4a25309751173ad5` |
| `87393706ebd6c57d19cd15a6d153d50a1b6375aa` | `ba9bb6fbd19b5d8696d57bd960b2f8e02fed3727` |
| `88cc69525c5727edab6c337fbd21fd02b5649f8d` | `0cc6f34dc330fabf3019a0bdb643a9b6418df683` |
| `8d0028697112d269d5257ac17562a3423f309c13` | `3a9900b24908d8f4b0ccbc0249425bdbce95eeef` |
| `8d35631abf21a909e8204e08103d10f8799185b3` | `cd405e7073fc65912f6b70e40d005c0874bda76d` |
| `8e10a7ff866a9dc7bd5a9ada0069379c5fc90704` | `e1b880b8f5c1c201f2fd6b4ebddcf9538e4a12b5` |
| `8fbdee7406111f077b5f580eaa8013b805880dd1` | `61c04fbb9d992871fb7bd21ab331988cdabc6476` |
| `90fe900747c4e55b1892a24f4eaf28839079c7b2` | `91834e58a06cfd427b2fba00de343e3d9d744ca3` |
| `916d4ae3271a159aed506f387b9b2777dcc61663` | `f3be97c03d6eafbc588fce66718f7239996d2d4d` |
| `91f2afca0fa58f40bd404b5154b9e5a76436f980` | `9a36e26f42cf23a843b3497bcfdeb011bc479baa` |
| `92eaaeb975463e844f1850e3212b1a080f14255e` | `6e615f66f94533c0469c2adf117f50206c096880` |
| `9348f00ae47dcb61d211de813deaa67e018f8f49` | `3c3411f883d43e1350f288e702b98b4802cd5ab1` |
| `9697a9ff7eb530e56962a3ade851d1897326ea58` | `fc2b1a8bfcf6ea7272594b6db1d121643e485b48` |
| `96c4801e954549371ef751f95867edd35862ef1f` | `5238dc07532f687edd42fc2cdf018a963300080f` |
| `96dd7539cc65a9784777138fe61fc56e6ba58366` | `e36549afb9fd52f99c8c6177da7dd5c5f675eef0` |
| `974b4f97f36ab71b39ea1461b8c26c5f4e4c573d` | `3f5d84a6c3912f710ce7d45fa4be3d41da3f90db` |
| `97d5421d0ab853a9e257abff92442a55557e9dd7` | `66d0caadfef1a2c7053eca77c44dde4d31fab56c` |
| `997fafe5f48baea4946bab81053c610c8fe8f414` | `2f352d5f2fdbec2774b5e10838c9feef3bebaeae` |
| `99e8d50074d79c3b4f0d89544afb113abf095089` | `1ba64da48d045858f52f692575dd2029d09fcbf7` |
| `9a9d95ad5f01a93514ad0f21b3551b7ac3ab9773` | `644ab2bb96b951164c4de64fd36d01b64b188783` |
| `9bcc384c1c50331a45b49b6e3fd7f95936096460` | `f6188150eefa010cb7a8c37a5b1d626663cb9da7` |
| `9cb8ac3fb8d42d9b19bb884991e358ee158b33ff` | `536e0d753cdbeb0e8b6d6aa855b9a30a6ff7bb30` |
| `9db727114f5b213e4f21fb171822fe2bf2511fee` | `871ebb74cfbf740d2ce8881adf56dbd51046ea18` |
| `9e2ca7aa4fda5e5315b559caf0042c9827ac9b23` | `6c21a5aa2138d79cecce54ecf23fa362733f7baf` |
| `9e901aff4c4e7872df4f1a6a26de6a585a98cceb` | `f20f8e9e61db439a92bd6c446968058f29552c3b` |
| `9f5986bccb69d93fd0aa76f0370f6b2a172660a9` | `594c9d29e5720d5d9c280802fbddb4ffc7af4456` |
| `a41efe1ff8ac4e97a38b4f4faeddea6697b2f1fc` | `1da1fce6d5e4137e76e62d080abb7ced0a11397b` |
| `a471c06d3761dce7b991a6c719031b5f563d4f18` | `58f04d1000c42932691069150e1b951dd560287f` |
| `a4f81342e2dae5a5352d823b7656a708af584468` | `7d7706171901a2bb3e06338621aa196f0484df44` |
| `a59069d89079f5bdb8f2bc829f591c1088917e7c` | `bbcef8c2579642eaac9c11139b2ad13bcfb0d312` |
| `aa9e768e22f0c5055b20439282a8960b1e672328` | `19cf73d025596c9bafdf5e0987aa4d75f092bc9a` |
| `ab32e8d45fea7335f1d81b67baccfffe0669569f` | `8cdfe805bca0c2e09cf83855cf130196c7ebd036` |
| `acf1a6664e48f5cfd52d9f1534f3d778381ad4e9` | `922d511a2ef76acc29e5a58b88eae055e7c105a2` |
| `ad46cebc97dc807f5325bc9ec4e394207a479fe2` | `bcca699cb7d9b79dbcbd3e958cdee9405550d8d6` |
| `add54d559f08f346064e63fbb78844e2ad803265` | `3f8eb7a940262f3c660b646a94c76ef3cab4846d` |
| `ae60d01bfcbdb2c15ae6c1394c15bc166da72e10` | `823dafb1007bcb44e549ca572578ff8d3b163246` |
| `af58629a5f49d17e4551d7caca2a6d0cd1879ab2` | `2618468cf54454d7573915495d0d718e2f5d0655` |
| `b2002facd9d9afd895179a05a33138619cfc0958` | `9899c530ee36419cd6a26a543e554b54d3e531de` |
| `b250a9f442c9d86b4be3127425875212a58388d8` | `ff28247ab076db7167f0bd704ffec1c9ca922fbc` |
| `b533695faa3333260ef495660fa0cf7062c5713a` | `d2c244a9a39cbd649ac89186504e40d9b49d9357` |
| `b56da1bf87b23902068a9d403d6d51b3025f16a9` | `50c43a165f5586c65be7419bab10a7ab00f608bf` |
| `b835d3e08339887f3a215efd6f4c201387d1c017` | `90b5208b131f4798e81db41ee8b6d1a2e9d51d66` |
| `bb921d0b35081a5a6ca607845c0a54cef7666cc9` | `6cd6d94e8398e6e979753ad61c4e0c593d4b125e` |
| `bbe25b279e8bd85968e44d5ab1f270305bbdec6a` | `063c9b797643f23a2d2c9d785bcc5ef734efa6ac` |
| `be506c486804b6443a02f8922added5dd0507b9e` | `c4e40bbf334a0e307f340fd63423d5d0aa1795f4` |
| `c1296cb2bedbee11501ab8da77be9210b9ec17ba` | `aba56080e095896a9041a938623824ba7ac136bf` |
| `c77486de6d5fc86c54db4298422d5d38f3b11b3b` | `4d48117806011a5ecfc580af1dbff98a0cc806f9` |
| `c830371a7e5fdd12a42dfa126cabcaf2a9ad9774` | `628b3a913352859b9eabd0b23555abff5655cf6a` |
| `c8413f3b754db38bb135aaebd770849da264eda6` | `cfdcb2344455a966ad7dece8fcf6e816bbf179ec` |
| `cbb16541861402f7c5a8c7c6ac78f1a39300b316` | `2f77582da7b89b27c30d8984a08d433d444d3d95` |
| `cbf600bb62f05f046b721264a753cdb8f1dfe258` | `f4a99c739c1843123e4456a5e122873daf0cbb67` |
| `ce65e5f84244a186a1bdc8eed649a3cb39187150` | `2fe92bd5687119fcc6f86c7841c53668d0862aee` |
| `d205e45ed18e19c4a5fb3f447827dc6b8d689c76` | `fa40bc7f56e40010403ae855885468fd8834d228` |
| `d424f1ace5bc91c268f0314282d6ec972008c057` | `f62c3f8c04879e5e51765bb92105b8d60ded6dec` |
| `d4fb81e47a0198469ad05bf73c7d5dd9dd4913b5` | `209d2ffc80acd42ccf45c3bbb4ef07637fcb668a` |
| `d677a0aa80062ea34bf5f1b03384fb17f5caea7f` | `de67c41d90c50695e5efc91b65880d754500ec10` |
| `d690c9aadc8e499390898d9255ce2789c63eb8bb` | `0dbd837bbb9ab8722cc29d71d1230a901a321862` |
| `d72e06a048d411fc95293a27bf4cf3eedfa99dff` | `398f3d9a3b11f7e8b2bf99a09680829dc92c4d6a` |
| `d8fc97cadef1f35bb57ddc8042d7261d8a80fece` | `60f532286d31474d74df8fa99d19c752286185e4` |
| `d9053f0e305d8a59c0254e3fcbb9db3ca9820fe5` | `acf602cbb406fd678e0f6d0b4439210745718959` |
| `d91f7911b9f34e2115fda3bc9c913a24084d508a` | `8f83f7087830e492ab4255cd11d2b4b942c03161` |
| `da9561861c13ce8c968bd85ec7a63944c5df508e` | `572e0a4c0556f5b8f355082d3433724e270e9c71` |
| `dac4dd4180fce273afec14da3577fb68d5192f8e` | `b24677da24f8c8a153e68bf71372c716bfe5e440` |
| `de5119b9dbb34807e3d098ffd186abca12dd0536` | `e57ef4af937aa2cb3b00a37831b93a9464f12a2c` |
| `df59080d3897f086eb944ee89c3a0577ff662dc0` | `d52f37a8d28d350ed6795227a9bc057692deacf1` |
| `e1b3ce75b7a18d39152687cb3042d991790803ec` | `62e80b7283c0a26c3628e94338321ac363a28eb8` |
| `e2dcb786d90e29e9ee9619b77553fef852af6e83` | `b1b91aa7b6a5320aa0ed85a5610270eb467931d1` |
| `e3bfff45ab3bbe8925e7bc73a564ff8be9885663` | `d939efc3e425e223ea58a0b5feef2d1dc43620d5` |
| `e443b40e738ef066b09a94363801c69893ffb38f` | `d6dcbde027ab16ad84e25a2f3d8a779b1cdcc0a8` |
| `e45722ff7cabb2234b1836ef09feb1fc9fbc8a55` | `02461515603dc6431ecc0a1fcee663f7fae9a0de` |
| `e78641fb6f22bcfef801523e61663af35ebb1938` | `1244e7ebc7497337faeab2d1a5e39531d48124b4` |
| `ea48794192bc71c87c19058b80709207ebef5aab` | `2afec068e8f23f5be19ee2dab0ca9a5c85987bea` |
| `ed52ec3e66952b96aaae792844cb3d7bb1edca7a` | `b1f18f66a71b15f3e2e62a0eaa443a8d891f78e2` |
| `ee9ea5afe67e3c54a826ac27033ad77b50fa7957` | `5458d33e4226346819e3b4c5aa7de9ed77b4a284` |
| `f0b343cf612b198354127d202eaf761a12a28975` | `2097fdfdcb82f2b956fa949a4d35ad92f414a49b` |
| `f21df50d615979a82bba3ccb64c1b0f23033fc92` | `ac7c670ded2757dee1af5282d2b4b9e989c4febd` |
| `f23c984aafdf91f94ed40fd29045ff090bcddbb1` | `f46de046d19d95ad87994c4ba8e9841d4e295298` |
| `f4a38ef917954b3fdd5c936fd7189f97e55b5841` | `2ca9878d68e45470e10123888d9f8109b2592306` |
| `f5da4f384aeb56c1600f9273bfc4ef1d2976b737` | `f2bbbf8a793275f4c456355a4f5bc5587ea311fe` |
| `f632d5f03b3a6a9eb4976aa112d9ab9a32673450` | `9065943594b8a6b7499f89599187c10963df9b15` |
| `f649416155d33d1fd78f416bc567750497d60f58` | `04f4186ed08bffc2d3209fb402e4cc2725787531` |
| `f6f9ae41d484cecf7a27e161d191fb21159b8906` | `7dfeff216542cd623e43682cffe1ce2c53a45393` |
| `f7e26d335c92dfa17db59464cda80d085f1173b9` | `d5500ab8b60f870f9f5f0440bdca05fc7df9b24a` |
| `f82986cebee3ebe58b2b24df82936a3cb8372519` | `9b65710ebe779cab6e8fb3b97d17cb732ad36938` |
| `fb916b2dbe63782d5cd9e08a24c139def46b294f` | `aa45bcd981a18a894a6857ffa65969472025b324` |
| `fbf708946cdf488602da1c189aa429a1ed0582f0` | `35f228ad5de11b3fe76322f287a23927b9cf5906` |
| `fe455b7d612c4edf9f0d6843bc6b8dac4abec721` | `fbe3d577254fb29f35d83bd334fe9ad5616e1093` |

Commits that became empty and were dropped: `2ef179e52f1b`
