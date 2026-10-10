import com.ipaengine.graph.GraphEngine;
import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.Method;

/** Real JVM/JNI smoke; Android's JSON stubs are bypassed only for diagnostics. */
public final class JniSmoke {
    private static void check(boolean condition, String description) {
        if (!condition) throw new AssertionError(description);
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
        System.err.println("JNI Unicode, NFC, policy, initialization and lifecycle smoke passed");
    }
}
