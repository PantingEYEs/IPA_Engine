import com.ipaengine.graph.GraphEngine;
import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.Method;

/** Real JVM/JNI smoke; Android's JSON stubs are bypassed only for diagnostics. */
public final class JniSmoke {
    private static void check(boolean condition, String description) {
        if (!condition) throw new AssertionError(description);
    }
    private static void printCase(String name, String json) {
        System.out.println("{\"jni_case\":\"" + name + "\",\"value\":" + json + "}");
    }
    public static void main(String[] arguments) throws Exception {
        try { new GraphEngine(arguments[0] + ".missing"); throw new AssertionError("missing path accepted"); }
        catch (IOException expected) { }
        GraphEngine value = new GraphEngine(arguments[0]);
        check(value.acceptsInput("a"), "ASCII input rejected");
        check(value.acceptsInput("a\u0301"), "Java NFC normalization missing");
        check(!value.acceptsInput("\ud800"), "isolated surrogate accepted");
        check(!value.acceptsInput("\udc00"), "isolated low surrogate accepted");
        check(!value.acceptsInput(new String(new char[65]).replace('\0', 'a')), "UTF-16 length guard missing");
        check(!value.acceptsInput("\ud840\udc00"), "unknown supplementary input accepted");
        check(!value.segmentPattern().isEmpty(), "segment pattern missing");
        check(value.query("").isEmpty(), "empty input produced prediction");
        check(value.query("\ud800").isEmpty(), "invalid Unicode produced candidates");
        Field handle = GraphEngine.class.getDeclaredField("nativeHandle");
        handle.setAccessible(true);
        Method query = GraphEngine.class.getDeclaredMethod("nativeQuery", long.class,
                String.class, String.class, String.class, int.class, boolean.class, int.class, int.class,
                boolean.class, int.class);
        query.setAccessible(true);
        for (String raw : new String[]{"a", "\u00e1", "\u0255o\u02d0", "\ud800", "\ud840\udc00"}) {
            System.out.println(query.invoke(null, handle.getLong(value), raw, "", "", 30, false, 60, 2048, false, 20000));
        }
        value.configure(new GraphEngine.Policy(false, false, true, false, false));
        System.out.println(query.invoke(null, handle.getLong(value), "a", "", "", 30, false, 60, 2048, false, 20000));
        value.configure(new GraphEngine.Policy());
        check(value.getOptions().allowOmissions, "default omissions disabled");
        System.out.println(query.invoke(null, handle.getLong(value), "a", "", "", 30, false, 60, 2048, true, 20000));
        value.close();
        value.close();
        try { value.acceptsInput("a"); throw new AssertionError("closed engine accepted call"); }
        catch (IllegalStateException expected) { }
        try { value.query("a"); throw new AssertionError("closed engine queried"); }
        catch (IllegalStateException expected) { }
        if (arguments.length > 1) {
            try (GraphEngine packs = new GraphEngine(arguments[1])) {
                check(packs.getPolicy().ruRU && packs.getPolicy().arStandard && packs.getPolicy().koKR,
                        "new languages did not default on");
                check(packs.acceptsInput("rʲiˈka"), "Russian pack alphabet missing");
                printCase("default-packs", packs.listPacksJson());
                printCase("russian", packs.reverseLookupJson("кот"));
                printCase("arabic", packs.reverseLookupJson("كتاب"));
                printCase("korean", packs.reverseLookupJson("한국"));
                printCase("reverse-nfc", packs.reverseLookupJson("e\u0301"));
                packs.setPackMode("ru", GraphEngine.PackMode.REVERSE_ONLY);
                check(!packs.acceptsInput("rʲiˈka"), "reverse-only pack accepted forward input");
                printCase("reverse-only", packs.listPacksJson());
                printCase("reverse-only-reading", packs.reverseLookupJson("кот"));
                Method legacy = GraphEngine.class.getDeclaredMethod("nativeConfigure", long.class,
                        boolean.class, boolean.class, boolean.class, boolean.class, boolean.class);
                legacy.setAccessible(true);
                legacy.invoke(null, handle.getLong(packs), false, false, false, false, false);
                printCase("legacy-policy", packs.reverseLookupJson("кот"));
                packs.configure(new GraphEngine.Policy(false, false, false, false, false, false, false, false));
                printCase("disabled-policy", packs.reverseLookupJson("кот"));
                packs.configure(new GraphEngine.Policy(false, false, false, false, false));
                printCase("five-argument-policy", packs.reverseLookupJson("кот"));
                check(new GraphEngine.Policy(false, false, false, false, false).koKR,
                        "five-argument policy did not preserve new default languages");
                packs.reverseLookupJson(new String(new char[1000]).replace('\0', 'a'));
                try {
                    packs.reverseLookupJson(new String(new char[1001]).replace('\0', 'a'));
                    throw new AssertionError("long reverse word accepted");
                } catch (IllegalArgumentException expected) { }
                try { packs.reverseLookupJson("\ud800"); throw new AssertionError("invalid reverse word accepted"); }
                catch (IllegalArgumentException expected) { }
                for (String id : new String[]{"core", "ru", "ar", "ko"}) {
                    packs.setPackMode(id, GraphEngine.PackMode.OFF);
                }
                check(packs.segmentPattern().equals("(?!)"), "disabled pack pattern remained cached");
                printCase("all-off", packs.listPacksJson());
                printCase("off-reading", packs.reverseLookupJson("кот"));
            }
            GraphEngine lazy = new GraphEngine(arguments[1], false);
            check(lazy.segmentPattern().equals("(?!)"), "lazy initialization loaded forward packs");
            printCase("lazy", lazy.listPacksJson());
            lazy.setPackMode("ru", GraphEngine.PackMode.INPUT);
            check(lazy.acceptsInput("rʲiˈka"), "lazy pack activation failed");
            lazy.close();
            try { lazy.listPacksJson(); throw new AssertionError("closed pack inventory accepted"); }
            catch (IllegalStateException expected) { }
            try { lazy.reverseLookupJson("кот"); throw new AssertionError("closed reverse accepted"); }
            catch (IllegalStateException expected) { }
            try (GraphEngine attached = new GraphEngine(arguments[2], false)) {
                attached.addDictionary(arguments[3], false);
                printCase("attached-lazy", attached.listPacksJson());
                attached.setPackMode("ru", GraphEngine.PackMode.INPUT);
                attached.setPackMode("core", GraphEngine.PackMode.INPUT);
                printCase("attached-reading", attached.reverseLookupJson("кот"));
                String before = attached.listPacksJson();
                try { attached.addDictionary(arguments[3], true); throw new AssertionError("duplicate pack accepted"); }
                catch (IOException expected) { }
                check(attached.listPacksJson().equals(before), "failed attachment changed live packs");
            }
        }
        System.err.println("JNI Unicode, NFC, policy, initialization and lifecycle smoke passed");
    }
}
